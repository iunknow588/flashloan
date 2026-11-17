//! USDG-denominated depth evidence for the token manager.
//!
//! Why this module exists
//! -----------------------
//!
//! The token manager's economic admission gate needs two things for every
//! token: a USDG valuation (how much one whole token is worth) and a USDG
//! depth (how much a pool around that token can carry). The valuation is
//! produced by the Uniswap Trading API and lands in a report file; the
//! depth is measured on chain by the pool manager.
//!
//! The two measurements have to meet somewhere. This module is that
//! meeting point: it reads the valuation report, multiplies it with the
//! measured token-denominated capacity of every pool, and writes the result
//! into `token_usdg_depth_measurements`.
//!
//! The table is a **separate evidence store**, not a column of the pool
//! snapshot: it answers a different question, it has a different lifetime,
//! and the token manager reads it with a read-only connection. Keeping it
//! separate means a valuation refresh never touches a pool observation and
//! a pool refresh never invents a valuation.
//!
//! What this module never does
//! ----------------------------
//!
//! * It never invents a measurement. A token without a USDG valuation, or a
//!   pool without a measured capacity, produces **no row**. A missing row
//!   means "not measured", which is a different statement from "measured as
//!   zero".
//! * It never writes a zero depth to express "too shallow". A zero depth is
//!   a measurement that says the pool cannot carry anything; the absence of
//!   a row is a measurement that says nobody looked.
//! * It never introduces a fiat price. USDG is the venue's own quote asset,
//!   so a depth expressed in USDG needs no external price feed.

use std::collections::BTreeMap;
use std::path::Path;

use rusqlite::Connection;
use serde_json::Value;

/// The canonical USDG address on Robinhood Chain.
pub const CANONICAL_USDG_ADDRESS: &str = "0x5fc5360d0400a0fd4f2af552add042d716f1d168";

/// USDG uses six decimals on this chain.
pub const USDG_DECIMALS: u8 = 6;

/// The native zero address. It is a placeholder, not a token: the
/// candidate pool tables carry it on one side of a pool whose real token
/// set is only known on chain.
pub const ZERO_TOKEN_ADDRESS: &str = "0x0000000000000000000000000000000000000000";

/// The largest depth that can be stored losslessly in the signed SQLite
/// `INTEGER` column of `token_usdg_depth_measurements`.
///
/// A depth is a measured quantity, and the measurement has to survive the
/// round trip through the database unchanged. `i64::MAX` is that limit.
pub const MAX_USDG_DEPTH_RAW: u128 = i64::MAX as u128;

/// Whether a token address can carry depth evidence.
///
/// The native zero address is rejected: it is a placeholder for an unknown
/// pool side, and `chain_tokens` does list it with eighteen decimals. A
/// valuation for it would therefore produce a depth row for an address
/// that identifies no token at all, and the token manager would read that
/// row as the measured depth of a real hop.
pub fn is_depth_bearing_token(token_address: &str) -> bool {
    let address = token_address.trim().to_ascii_lowercase();
    !address.is_empty() && address != ZERO_TOKEN_ADDRESS
}

/// One token's USDG valuation, read from a `value-tokens` report.
#[derive(Clone, Debug)]
pub struct UsdgValuation {
    /// Token address, lowercase.
    pub token_address: String,
    /// The value of one whole token, in raw USDG units (six decimals).
    pub value_usdg_raw: u128,
}

/// Reads the USDG valuations from a `value-tokens` report document.
///
/// The document may be the rendered CLI output (which embeds the batch
/// report under `report`) or a bare batch report. Both shapes are accepted
/// so the operator can hand the pool manager exactly the file the token
/// manager produced.
///
/// Only tokens with a `route_found` verdict and a parseable output amount
/// are returned. A `no_route` verdict is a real negative and carries no
/// value; an `api_error` or `api_unavailable` verdict is an unknown and
/// carries no value either. Both are simply absent from the result, so the
/// caller cannot accidentally treat "not measured" as "worth zero".
pub fn read_usdg_valuations(path: &Path) -> Result<Vec<UsdgValuation>, String> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let raw = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read valuation report {}: {error}", path.display()))?;
    let document: Value = serde_json::from_str(&raw).map_err(|error| {
        format!(
            "valuation report {} is not valid JSON: {error}",
            path.display()
        )
    })?;
    let tokens = document
        .get("report")
        .and_then(|report| report.get("tokens"))
        .or_else(|| document.get("tokens"))
        .and_then(Value::as_array)
        .ok_or_else(|| format!("valuation report {} carries no token list", path.display()))?;

    let mut valuations = Vec::new();
    for token in tokens {
        let Some(address) = token.get("token_address").and_then(Value::as_str) else {
            continue;
        };
        let Some(verdict) = token.get("verdict").and_then(Value::as_str) else {
            continue;
        };
        if verdict != "route_found" {
            continue;
        }
        let Some(raw_amount) = token.get("usdg_amount_raw").and_then(Value::as_str) else {
            continue;
        };
        let Ok(value) = raw_amount.trim().parse::<u128>() else {
            continue;
        };
        valuations.push(UsdgValuation {
            token_address: address.trim().to_ascii_lowercase(),
            value_usdg_raw: value,
        });
    }
    valuations.sort_by(|left, right| left.token_address.cmp(&right.token_address));
    Ok(valuations)
}

/// Reads the USDG valuations from the durable valuation store.
///
/// The report file is a transient artifact: it can be moved, regenerated
/// or lost. The durable store is the statement the system actually relies
/// on, so the pool manager is able to read the valuations from there
/// instead of from a file.
///
/// Only rows with a `route_found` verdict and a stored value are returned.
/// A `no_route` verdict is a real negative and carries no value; an
/// `api_error` or `api_unavailable` verdict is an unknown and carries no
/// value either. Both are simply absent from the result, so the caller
/// cannot accidentally treat "not measured" as "worth zero".
///
/// The reader opens the store read-only: the pool manager consumes the
/// valuation evidence, it never writes it.
pub fn read_usdg_valuations_from_db(
    path: &Path,
    network: &str,
    chain_id: i64,
) -> Result<Vec<UsdgValuation>, String> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let connection = Connection::open_with_flags(
        path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_URI,
    )
    .map_err(|error| format!("cannot open valuation store {}: {error}", path.display()))?;

    let mut statement = connection
        .prepare(
            "SELECT token_address, usdg_amount_raw
               FROM token_usdg_valuations
              WHERE network = ?1
                AND chain_id = ?2
                AND verdict = 'route_found'
                AND usdg_amount_raw IS NOT NULL",
        )
        .map_err(|error| format!("cannot query valuation store: {error}"))?;

    // The raw amount is read as a decimal string inside the row closure:
    // the store keeps it as an exact decimal string, so a value never
    // loses a digit to a floating point round trip. The column is typed
    // TEXT, but an INTEGER written by another tool is accepted too: the
    // number is the same number either way.
    let rows = statement
        .query_map(rusqlite::params![network, chain_id], |row| {
            let address = row.get::<_, String>(0)?;
            let text = match row.get_ref(1)? {
                rusqlite::types::ValueRef::Integer(value) => value.to_string(),
                rusqlite::types::ValueRef::Text(bytes) => std::str::from_utf8(bytes)
                    .unwrap_or_default()
                    .trim()
                    .to_string(),
                _ => String::new(),
            };
            Ok((address, text))
        })
        .map_err(|error| format!("cannot read valuation store: {error}"))?;

    let mut valuations = Vec::new();
    for row in rows {
        let (address, text) =
            row.map_err(|error| format!("cannot read valuation store: {error}"))?;
        if text.is_empty() {
            continue;
        }
        let Ok(value) = text.parse::<u128>() else {
            continue;
        };
        let address = address.trim().to_ascii_lowercase();
        if !is_depth_bearing_token(&address) {
            continue;
        }
        valuations.push(UsdgValuation {
            token_address: address,
            value_usdg_raw: value,
        });
    }
    valuations.sort_by(|left, right| left.token_address.cmp(&right.token_address));
    Ok(valuations)
}

/// Creates the `token_usdg_depth_measurements` table when it is missing.
///
/// The table is created by the tool that *writes* the evidence. The token
/// manager only ever opens the database read-only, so it cannot create the
/// table itself, and it must not: a reader that can create the schema it
/// depends on can silently materialise an empty evidence set.
pub fn ensure_usdg_depth_schema(connection: &Connection) -> Result<(), String> {
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS token_usdg_depth_measurements (
                 network TEXT NOT NULL,
                 chain_id INTEGER NOT NULL,
                 token_address TEXT NOT NULL,
                 pool_id TEXT NOT NULL,
                 depth_usdg_raw INTEGER NOT NULL,
                 token_decimals INTEGER,
                 valuation_usdg_raw INTEGER,
                 price_impact_bps REAL,
                 pool_state_version TEXT,
                 measured_at TEXT NOT NULL,
                 PRIMARY KEY (network, chain_id, token_address, pool_id)
             );
             CREATE INDEX IF NOT EXISTS idx_token_usdg_depth_measurements_token
                 ON token_usdg_depth_measurements (network, chain_id, token_address);",
        )
        .map_err(|error| error.to_string())
}

/// One measured pool-side depth observation, ready to be written.
#[derive(Clone, Debug)]
pub struct UsdgDepthObservation {
    /// Token address, lowercase.
    pub token_address: String,
    /// The pool the depth was measured in.
    pub pool_id: String,
    /// The depth, in raw USDG units.
    pub depth_usdg_raw: u128,
    /// The decimals of the token the capacity was measured in.
    pub token_decimals: Option<i64>,
    /// The USDG valuation used to convert the capacity, in raw USDG units.
    pub valuation_usdg_raw: Option<u128>,
    /// The price impact the measured capacity was reached at.
    pub price_impact_bps: Option<f64>,
    /// A version marker for the pool state the measurement came from.
    pub pool_state_version: Option<String>,
}

/// Converts a measured token-denominated capacity into a USDG depth.
///
/// The capacity is measured in the token's own raw units and the valuation
/// is the value of one whole token in raw USDG units, so the product has to
/// be divided by `10^token_decimals` to land in raw USDG units:
///
/// `depth_usdg_raw = capacity_raw * value_usdg_raw / 10^token_decimals`
///
/// The division is done with integer arithmetic on a `u128` intermediate so
/// no rounding error can creep into the evidence. The result is truncated
/// downwards: a depth that is slightly overstated would admit a position
/// the pool cannot actually carry.
pub fn capacity_to_usdg_depth(
    capacity_raw: u128,
    token_decimals: Option<i64>,
    valuation_usdg_raw: Option<u128>,
) -> Option<u128> {
    let decimals = token_decimals?;
    if !(0..=38).contains(&decimals) {
        return None;
    }
    let valuation = valuation_usdg_raw?;
    if capacity_raw == 0 || valuation == 0 {
        return None;
    }
    let divisor = 10u128.checked_pow(decimals as u32)?;
    // The intermediate product can overflow for absurd inputs, so it is
    // widened through a checked multiply. A capacity that cannot be
    // represented is not evidence.
    let numerator = capacity_raw.checked_mul(valuation)?;
    let depth = numerator / divisor;
    // The depth is stored in a signed SQLite INTEGER column, so a value
    // that does not fit in an i64 cannot be evidence. Such a depth would be
    // written as a floating point number and lose its precision, and the
    // token manager reads the column as an integer. A depth this large is
    // not a pool that carries a position, it is a measurement that cannot
    // be represented, so it stays unmeasured.
    if depth > MAX_USDG_DEPTH_RAW {
        return None;
    }
    Some(depth)
}

/// Writes the USDG depth observations.
///
/// The observations are keyed by `(network, chain_id, token_address,
/// pool_id)` so a later measurement of the same pool replaces the earlier
/// one instead of accumulating stale rows. A pool that is no longer
/// measured keeps its previous row; removing it is the job of an explicit
/// maintenance pass, not of a measurement run, because "not refreshed" must
/// never silently become "not deep".
pub fn write_usdg_depth_observations(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    measured_at: &str,
    observations: &[UsdgDepthObservation],
) -> Result<usize, String> {
    ensure_usdg_depth_schema(connection)?;
    let mut statement = connection
        .prepare(
            "INSERT INTO token_usdg_depth_measurements (
                 network, chain_id, token_address, pool_id, depth_usdg_raw,
                 token_decimals, valuation_usdg_raw, price_impact_bps,
                 pool_state_version, measured_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)
             ON CONFLICT (network, chain_id, token_address, pool_id) DO UPDATE SET
                 depth_usdg_raw = excluded.depth_usdg_raw,
                 token_decimals = excluded.token_decimals,
                 valuation_usdg_raw = excluded.valuation_usdg_raw,
                 price_impact_bps = excluded.price_impact_bps,
                 pool_state_version = excluded.pool_state_version,
                 measured_at = excluded.measured_at",
        )
        .map_err(|error| error.to_string())?;
    let mut written = 0usize;
    for observation in observations {
        // The row is skipped rather than written when the observation is not
        // representable: a placeholder address or an out-of-range depth
        // would be stored as a fact about a token that does not exist, or
        // as a floating point number that silently loses precision.
        if !is_depth_bearing_token(&observation.token_address) {
            continue;
        }
        if observation.depth_usdg_raw == 0 || observation.depth_usdg_raw > MAX_USDG_DEPTH_RAW {
            continue;
        }
        // The depth is written as a decimal string so that no intermediate
        // conversion can round it. SQLite applies the INTEGER affinity and
        // stores the value losslessly, because the range check above has
        // already proven that it fits into an i64.
        written += statement
            .execute(rusqlite::params![
                network,
                chain_id,
                observation.token_address,
                observation.pool_id,
                observation.depth_usdg_raw.to_string(),
                observation.token_decimals,
                observation
                    .valuation_usdg_raw
                    .map(|value| value.to_string()),
                observation.price_impact_bps,
                observation.pool_state_version,
                measured_at,
            ])
            .map_err(|error| error.to_string())? as usize;
    }
    Ok(written)
}

/// Indexes the valuations by token address for lookups.
pub fn valuation_index(valuations: &[UsdgValuation]) -> BTreeMap<String, u128> {
    valuations
        .iter()
        .map(|valuation| (valuation.token_address.clone(), valuation.value_usdg_raw))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_valuation_report_yields_only_the_routed_tokens() {
        let document = json!({
            "report": {
                "tokens": [
                    {"token_address": "0xAAA", "verdict": "route_found", "usdg_amount_raw": "1500000"},
                    {"token_address": "0xBBB", "verdict": "no_route"},
                    {"token_address": "0xCCC", "verdict": "api_error"},
                    {"token_address": "0xDDD", "verdict": "route_found"}
                ]
            }
        });
        let path =
            std::env::temp_dir().join(format!("usdg_depth_valuation_{}.json", std::process::id()));
        std::fs::write(&path, serde_json::to_string(&document).unwrap()).unwrap();
        let valuations = read_usdg_valuations(&path).unwrap();
        let _ = std::fs::remove_file(&path);
        assert_eq!(valuations.len(), 1);
        assert_eq!(valuations[0].token_address, "0xaaa");
        assert_eq!(valuations[0].value_usdg_raw, 1_500_000);
    }

    #[test]
    fn a_missing_report_is_an_empty_result_not_an_error() {
        let path = std::env::temp_dir().join("usdg_depth_report_that_does_not_exist.json");
        assert!(read_usdg_valuations(&path).unwrap().is_empty());
    }

    #[test]
    fn capacity_is_converted_into_usdg_with_integer_arithmetic() {
        // 2 whole tokens of an 18-decimal token worth 1.5 USDG carry
        // 3 USDG, which is 3_000_000 raw USDG units.
        let depth =
            capacity_to_usdg_depth(2_000_000_000_000_000_000, Some(18), Some(1_500_000)).unwrap();
        assert_eq!(depth, 3_000_000);
    }

    #[test]
    fn a_missing_measurement_stays_missing() {
        // No decimals, no valuation and a zero capacity all mean "not
        // measured". None of them may be turned into a zero depth.
        assert_eq!(capacity_to_usdg_depth(1_000, None, Some(1)), None);
        assert_eq!(capacity_to_usdg_depth(1_000, Some(18), None), None);
        assert_eq!(capacity_to_usdg_depth(0, Some(18), Some(1)), None);
    }

    #[test]
    fn the_depth_table_round_trips_through_sqlite() {
        let connection = Connection::open_in_memory().unwrap();
        ensure_usdg_depth_schema(&connection).unwrap();
        let observations = vec![UsdgDepthObservation {
            token_address: "0xaaa".to_string(),
            pool_id: "0xpool".to_string(),
            depth_usdg_raw: 3_000_000,
            token_decimals: Some(18),
            valuation_usdg_raw: Some(1_500_000),
            price_impact_bps: Some(9.5),
            pool_state_version: Some("v1".to_string()),
        }];
        let written = write_usdg_depth_observations(
            &connection,
            "robinhood",
            4663,
            "2026-11-12T00:00:00Z",
            &observations,
        )
        .unwrap();
        assert_eq!(written, 1);
        let read: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM token_usdg_depth_measurements
                  WHERE network = 'robinhood' AND chain_id = 4663
                    AND token_address = '0xaaa' AND depth_usdg_raw > 0",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(read, 1);
    }

    #[test]
    fn a_depth_that_cannot_fit_an_integer_stays_unmeasured() {
        // A capacity whose USDG value exceeds i64::MAX cannot be stored in
        // the signed INTEGER column. It must not be turned into a floating
        // point number, and it must not be written as evidence.
        let oversized = MAX_USDG_DEPTH_RAW + 1;
        assert_eq!(
            capacity_to_usdg_depth(1_000_000, Some(0), Some(oversized)),
            None
        );
        // The largest representable depth is still accepted.
        assert_eq!(
            capacity_to_usdg_depth(1, Some(0), Some(MAX_USDG_DEPTH_RAW)),
            Some(MAX_USDG_DEPTH_RAW)
        );
    }

    #[test]
    fn the_placeholder_address_carries_no_depth_evidence() {
        assert!(!is_depth_bearing_token(ZERO_TOKEN_ADDRESS));
        assert!(!is_depth_bearing_token(
            "0x0000000000000000000000000000000000000000"
        ));
        assert!(!is_depth_bearing_token(""));
        assert!(is_depth_bearing_token("0xaaa"));
    }

    #[test]
    fn a_placeholder_address_never_becomes_a_row() {
        let connection = Connection::open_in_memory().unwrap();
        ensure_usdg_depth_schema(&connection).unwrap();
        let observations = vec![
            UsdgDepthObservation {
                token_address: ZERO_TOKEN_ADDRESS.to_string(),
                pool_id: "0xpool".to_string(),
                depth_usdg_raw: 3_000_000,
                token_decimals: Some(18),
                valuation_usdg_raw: Some(1_500_000),
                price_impact_bps: Some(9.5),
                pool_state_version: None,
            },
            UsdgDepthObservation {
                token_address: "0xbbb".to_string(),
                pool_id: "0xpool".to_string(),
                depth_usdg_raw: 3_000_000,
                token_decimals: Some(18),
                valuation_usdg_raw: Some(1_500_000),
                price_impact_bps: Some(9.5),
                pool_state_version: None,
            },
        ];
        let written = write_usdg_depth_observations(
            &connection,
            "robinhood",
            4663,
            "2026-11-12T00:00:00Z",
            &observations,
        )
        .unwrap();
        assert_eq!(written, 1);
        let count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM token_usdg_depth_measurements",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(count, 1);
        let address: String = connection
            .query_row(
                "SELECT token_address FROM token_usdg_depth_measurements",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(address, "0xbbb");
    }

    #[test]
    fn a_depth_beyond_the_integer_range_is_not_written() {
        let connection = Connection::open_in_memory().unwrap();
        ensure_usdg_depth_schema(&connection).unwrap();
        let observations = vec![UsdgDepthObservation {
            token_address: "0xccc".to_string(),
            pool_id: "0xpool".to_string(),
            depth_usdg_raw: MAX_USDG_DEPTH_RAW + 1,
            token_decimals: Some(18),
            valuation_usdg_raw: Some(1_500_000),
            price_impact_bps: Some(9.5),
            pool_state_version: None,
        }];
        let written = write_usdg_depth_observations(
            &connection,
            "robinhood",
            4663,
            "2026-11-12T00:00:00Z",
            &observations,
        )
        .unwrap();
        assert_eq!(written, 0);
        let count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM token_usdg_depth_measurements",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(count, 0);
    }

    #[test]
    fn a_stored_depth_is_readable_as_an_integer() {
        // The regression this protects: a depth written as a string must
        // land in the column as an INTEGER, not as a REAL. A REAL would
        // lose precision and the token manager's `row.get::<_, i64>` would
        // fail on it.
        let connection = Connection::open_in_memory().unwrap();
        ensure_usdg_depth_schema(&connection).unwrap();
        let observations = vec![UsdgDepthObservation {
            token_address: "0xddd".to_string(),
            pool_id: "0xpool".to_string(),
            depth_usdg_raw: 3_000_000,
            token_decimals: Some(18),
            valuation_usdg_raw: Some(1_500_000),
            price_impact_bps: Some(9.5),
            pool_state_version: None,
        }];
        write_usdg_depth_observations(
            &connection,
            "robinhood",
            4663,
            "2026-11-12T00:00:00Z",
            &observations,
        )
        .unwrap();
        let (kind, value): (String, i64) = connection
            .query_row(
                "SELECT typeof(depth_usdg_raw), depth_usdg_raw
                   FROM token_usdg_depth_measurements",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(kind, "integer");
        assert_eq!(value, 3_000_000);
    }

    #[test]
    fn remeasuring_a_pool_replaces_the_previous_row() {
        let connection = Connection::open_in_memory().unwrap();
        ensure_usdg_depth_schema(&connection).unwrap();
        for depth in [3_000_000u128, 4_000_000u128] {
            let observations = vec![UsdgDepthObservation {
                token_address: "0xaaa".to_string(),
                pool_id: "0xpool".to_string(),
                depth_usdg_raw: depth,
                token_decimals: Some(18),
                valuation_usdg_raw: Some(1_500_000),
                price_impact_bps: Some(9.5),
                pool_state_version: None,
            }];
            write_usdg_depth_observations(
                &connection,
                "robinhood",
                4663,
                "2026-11-12T00:00:00Z",
                &observations,
            )
            .unwrap();
        }
        let (count, depth): (i64, i64) = connection
            .query_row(
                "SELECT COUNT(*), MAX(depth_usdg_raw) FROM token_usdg_depth_measurements",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(count, 1);
        assert_eq!(depth, 4_000_000);
    }
}
