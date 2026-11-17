//! P0-5 probe: audit the USDG valuation expiry policy.
//!
//! The probe runs against a **throwaway copy** of the manager database.
//! It seeds the `token_usdg_valuations` table with observations of
//! deliberately different ages and verdict classes, then reads the expiry
//! state back through `token_manager::audit_valuation_expiry`.
//!
//! The point of the seeded population is that every interesting
//! combination has to be exercised at once:
//!
//! * a fresh positive and a fresh negative (both still usable);
//! * a two-hour-old positive (fresh for membership, stale for magnitude);
//! * a two-day-old negative (stale for membership);
//! * a stored probe failure (unknown, never a negative);
//! * a row whose timestamp cannot be parsed (unverifiable);
//! * a row whose timestamp lies in the future (stale).
//!
//! Nothing here touches a production database: the copy is created in a
//! temporary directory and deleted by the caller.

use rusqlite::{params, Connection};
use std::path::Path;
use token_manager::{audit_valuation_expiry, current_epoch_seconds};

const NETWORK: &str = "robinhood";
const CHAIN_ID: u64 = 4663;

/// One seeded valuation row.
struct Seed {
    address: &'static str,
    verdict: &'static str,
    age_seconds: Option<i64>,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("manager.sqlite3");
    manager_db::migrate(&path)?;
    seed(&path)?;

    let report = audit_valuation_expiry(&path, NETWORK, CHAIN_ID)?;

    let payload = serde_json::json!({
        "schemaVersion": "p0-5-valuation-expiry/1",
        "stage": "P0-5",
        "component": "token_manager",
        "mode": "read_only",
        "productionWrite": false,
        "network": report.network,
        "chainId": report.chain_id,
        "rowCount": report.row_count,
        "membershipMaxAgeSeconds": report.membership_max_age_seconds,
        "magnitudeMaxAgeSeconds": report.magnitude_max_age_seconds,
        "membershipFreshCount": report.membership_fresh_count,
        "membershipStaleCount": report.membership_stale_count,
        "membershipUnverifiableCount": report.membership_unverifiable_count,
        "magnitudeFreshCount": report.magnitude_fresh_count,
        "magnitudeStaleCount": report.magnitude_stale_count,
        "magnitudeUnverifiableCount": report.magnitude_unverifiable_count,
        "positiveVerdictCount": report.positive_verdict_count,
        "negativeVerdictCount": report.negative_verdict_count,
        "unknownVerdictCount": report.unknown_verdict_count,
        "staleMembershipTokens": report.stale_membership_tokens,
        "staleMagnitudeTokens": report.stale_magnitude_tokens,
    });
    println!("{}", serde_json::to_string_pretty(&payload)?);
    Ok(())
}

/// Seeds the valuation table with a deliberately mixed population.
fn seed(path: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let connection = Connection::open(path)?;
    connection.execute_batch(
        "CREATE TABLE IF NOT EXISTS token_usdg_valuations (
             network TEXT NOT NULL,
             chain_id INTEGER NOT NULL,
             token_address TEXT NOT NULL,
             token_symbol TEXT,
             verdict TEXT NOT NULL,
             has_value INTEGER NOT NULL,
             input_amount_raw TEXT NOT NULL,
             usdg_amount_raw TEXT,
             usdg_amount_decimal TEXT,
             price_impact TEXT,
             gas_estimate TEXT,
             route_leg_count INTEGER,
             protocols_json TEXT NOT NULL,
             failure_kind TEXT,
             failure_detail TEXT,
             source TEXT NOT NULL,
             measured_at TEXT NOT NULL,
             PRIMARY KEY (network, chain_id, token_address))",
    )?;

    let now = current_epoch_seconds();
    let seeds = [
        // Fresh positive: usable for both membership and magnitude.
        Seed {
            address: "0x0000000000000000000000000000000000000001",
            verdict: "route_found",
            age_seconds: Some(60),
        },
        // Fresh negative: a real negative while it is fresh.
        Seed {
            address: "0x0000000000000000000000000000000000000002",
            verdict: "no_route",
            age_seconds: Some(120),
        },
        // Two hours old: fresh for membership, stale for magnitude.
        Seed {
            address: "0x0000000000000000000000000000000000000003",
            verdict: "route_found",
            age_seconds: Some(7200),
        },
        // Two days old: stale for membership and for magnitude.
        Seed {
            address: "0x0000000000000000000000000000000000000004",
            verdict: "no_route",
            age_seconds: Some(2 * 24 * 3600),
        },
        // A stored probe failure: unknown, and it must stay unknown.
        Seed {
            address: "0x0000000000000000000000000000000000000005",
            verdict: "api_error",
            age_seconds: Some(30),
        },
        // An unparseable timestamp: unverifiable, so fail closed.
        Seed {
            address: "0x0000000000000000000000000000000000000006",
            verdict: "route_found",
            age_seconds: None,
        },
        // A future timestamp: stale, so fail closed.
        Seed {
            address: "0x0000000000000000000000000000000000000007",
            verdict: "route_found",
            age_seconds: Some(-600),
        },
    ];

    for seed in &seeds {
        let measured_at = match seed.age_seconds {
            Some(offset) => rfc3339(now - offset),
            None => "not-a-timestamp".to_owned(),
        };
        connection.execute(
            "INSERT INTO token_usdg_valuations (
                 network, chain_id, token_address, token_symbol, verdict,
                 has_value, input_amount_raw, usdg_amount_raw,
                 usdg_amount_decimal, price_impact, gas_estimate,
                 route_leg_count, protocols_json, failure_kind,
                 failure_detail, source, measured_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17)",
            params![
                NETWORK,
                CHAIN_ID as i64,
                seed.address,
                "TKN",
                seed.verdict,
                if seed.verdict == "route_found" { 1 } else { 0 },
                "1000000",
                if seed.verdict == "route_found" {
                    Some("2044537553")
                } else {
                    None
                },
                if seed.verdict == "route_found" {
                    Some("2044.537553")
                } else {
                    None
                },
                None::<String>,
                None::<String>,
                None::<i64>,
                "[]",
                None::<String>,
                None::<String>,
                "uniswap_trading_api_quote",
                measured_at,
            ],
        )?;
    }
    Ok(())
}

/// Renders an epoch second as an RFC-3339 UTC timestamp.
fn rfc3339(epoch_seconds: i64) -> String {
    let days = epoch_seconds.div_euclid(86_400);
    let remainder = epoch_seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}+00:00",
        year,
        month,
        day,
        remainder / 3600,
        (remainder % 3600) / 60,
        remainder % 60
    )
}

/// The inverse of Howard Hinnant's `days_from_civil`.
fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let day_of_era = z - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_position = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_position + 2) / 5 + 1;
    let month = if month_position < 10 {
        month_position + 3
    } else {
        month_position - 9
    };
    let year = if month <= 2 { year + 1 } else { year };
    (year, month, day)
}
