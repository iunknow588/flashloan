//! Bridge between the USDG valuation probe, the measured pool depth and
//! the economic admission gate.
//!
//! The gate in [`crate::classification`] needs three inputs that live in
//! three different places:
//!
//! * a **USDG valuation** per stock token, measured through the Uniswap
//!   Trading API (`/quote`);
//! * a **pool depth** per intermediate hop, measured on chain by the pool
//!   manager and expressed in USDG;
//! * the set of tokens that were **actually measured**, which is what
//!   separates "cannot be valued" from "not looked at yet".
//!
//! This module assembles those into an [`EconomicEvidence`] and reports
//! what the refresh actually observed, so an operator can tell a coverage
//! gap from a defect.

use std::collections::BTreeMap;
use std::error::Error;

use rusqlite::{Connection, OpenFlags};

use crate::economic_gate::EconomicEvidence;
use crate::usdg_economic::{usdg_to_decimal, EconomicPolicy, USDG_DECIMALS};
use crate::valuation::{ValuationBatchReport, ValuationVerdict};

/// The native zero address. It is a placeholder for an unknown pool side,
/// not a token, so it can never carry depth evidence.
pub const ZERO_TOKEN_ADDRESS: &str = "0x0000000000000000000000000000000000000000";

/// Whether a token address can carry depth evidence.
///
/// The zero address is rejected: it identifies no token, so a measured
/// depth attributed to it describes no tradable hop.
pub fn is_depth_bearing_token(token_address: &str) -> bool {
    let address = token_address.trim().to_ascii_lowercase();
    !address.is_empty() && address != ZERO_TOKEN_ADDRESS
}

/// The USDG pool depth of one token, measured by the pool manager.
#[derive(Clone, Debug)]
pub struct MeasuredDepth {
    /// The token address, lowercase.
    pub token_address: String,
    /// The depth in raw USDG units.
    pub depth_usdg_raw: u128,
}

/// The outcome of assembling the economic evidence.
#[derive(Clone, Debug)]
pub struct EvidenceReport {
    /// The evidence itself.
    pub evidence: EconomicEvidence,
    /// How many stock tokens were measured.
    pub measured_stock_count: usize,
    /// How many stock tokens were found to have a live USDG path.
    pub valued_stock_count: usize,
    /// How many stock tokens were measured but had no live path.
    pub unvaluable_stock_count: usize,
    /// How many stock tokens could not be measured at all.
    pub unknown_stock_count: usize,
    /// How many intermediate hops had a measured depth.
    pub measured_extension_count: usize,
    /// How many intermediate hops were measured but too shallow.
    pub dust_extension_count: usize,
    /// How many intermediate hops had no measured depth.
    pub unmeasured_extension_count: usize,
}

/// Builds the evidence from a valuation report and a set of measured depths.
///
/// The distinction that matters most here is between **unknown** and
/// **negative**. A token whose probe came back `api_unavailable` or
/// `api_error` is *unknown*: the refresh did not establish anything about
/// it, so it must not be excluded. Only a `no_route` verdict — the
/// service answering with "there is no path" — is a genuine negative.
pub fn build_evidence(
    valuation: Option<&ValuationBatchReport>,
    depths: &[MeasuredDepth],
) -> EvidenceReport {
    let mut evidence = EconomicEvidence::default();
    let mut measured_stock_count = 0usize;
    let mut valued_stock_count = 0usize;
    let mut unvaluable_stock_count = 0usize;
    let mut unknown_stock_count = 0usize;

    if let Some(report) = valuation {
        for token in &report.tokens {
            let address = token.token_address.to_ascii_lowercase();
            match token.verdict {
                ValuationVerdict::RouteFound => {
                    measured_stock_count += 1;
                    if let Some(raw) = token.usdg_amount_raw.as_deref() {
                        if let Ok(value) = raw.parse::<u128>() {
                            evidence
                                .stock_values_usdg_raw
                                .insert(address.clone(), value);
                            valued_stock_count += 1;
                        }
                    }
                    evidence.measured_stock_addresses.insert(address);
                }
                ValuationVerdict::NoRoute => {
                    measured_stock_count += 1;
                    unvaluable_stock_count += 1;
                    evidence.measured_stock_addresses.insert(address);
                }
                ValuationVerdict::ApiUnavailable | ValuationVerdict::ApiError => {
                    unknown_stock_count += 1;
                }
            }
        }
    }

    let mut measured_extension_count = 0usize;
    let mut dust_extension_count = 0usize;
    for depth in depths {
        measured_extension_count += 1;
        if depth.depth_usdg_raw < EconomicPolicy::default().min_pool_depth_usdg_raw {
            dust_extension_count += 1;
        }
        evidence.extension_depth_usdg_raw.insert(
            depth.token_address.to_ascii_lowercase(),
            depth.depth_usdg_raw,
        );
    }

    EvidenceReport {
        evidence,
        measured_stock_count,
        valued_stock_count,
        unvaluable_stock_count,
        unknown_stock_count,
        measured_extension_count,
        dust_extension_count,
        unmeasured_extension_count: 0,
    }
}

/// Reads a valuation report from a `value-tokens` CLI output document.
///
/// The document has two accepted shapes: the rendered CLI output, which
/// embeds the authoritative batch report under `report`, and a bare batch
/// report. Both are read with the same rules, so the operator can hand
/// back whatever `value-tokens` printed.
pub fn read_valuation_report(
    path: &std::path::Path,
) -> Result<Option<crate::valuation::ValuationBatchReport>, Box<dyn Error>> {
    if !path.exists() {
        return Ok(None);
    }
    let body = std::fs::read_to_string(path)?;
    let document: serde_json::Value = serde_json::from_str(&body)?;
    if let Some(embedded) = document.get("report") {
        return Ok(Some(serde_json::from_value(embedded.clone())?));
    }
    // A probe artifact that carries only the per-token rows. The batch
    // counters are not observations, so they are rebuilt from the rows
    // themselves rather than trusted from an envelope that may describe a
    // different population than the rows below it.
    if document.get("tokens").is_some() && document.get("source").is_none() {
        let tokens: Vec<crate::valuation::TokenValuation> = serde_json::from_value(
            document
                .get("tokens")
                .cloned()
                .ok_or("valuation report has no token list")?,
        )?;
        return Ok(Some(crate::valuation::ValuationBatchReport::from_tokens(
            tokens,
        )));
    }
    Ok(Some(serde_json::from_value(document)?))
}

/// Reads the measured USDG depth of every token from a pool-manager
/// snapshot database.
///
/// The pool manager stores the capacity of a pool in the token it is
/// measured in, together with the capacity of the other side. A token's
/// usable depth is the capacity of the pool *as seen from that token*,
/// which is what decides whether the token can carry the position that
/// enters it.
///
/// The database is opened **read-only**: the token manager never writes to
/// the pool manager's snapshot store.
pub fn read_measured_depths(
    database: &std::path::Path,
    network: &str,
    chain_id: u64,
) -> Result<Vec<MeasuredDepth>, Box<dyn Error>> {
    if !database.exists() {
        return Ok(Vec::new());
    }
    let connection = Connection::open_with_flags(
        database,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI,
    )?;
    // A database that has never had a depth refresh has no measurement
    // table. That is "nothing was measured", not an error: the gate can
    // still run on the valuation alone, and an unmeasured hop stays
    // pending rather than being excluded.
    let table_present: i64 = connection.query_row(
        "SELECT COUNT(*) FROM sqlite_master
          WHERE type = 'table' AND name = 'token_usdg_depth_measurements'",
        [],
        |row| row.get(0),
    )?;
    if table_present == 0 {
        return Ok(Vec::new());
    }
    let mut statement = connection.prepare(
        "SELECT token_address, depth_usdg_raw
           FROM token_usdg_depth_measurements
          WHERE network = ?1 AND chain_id = ?2 AND depth_usdg_raw > 0",
    )?;
    let rows = statement.query_map(rusqlite::params![network, chain_id as i64], |row| {
        Ok(MeasuredDepth {
            token_address: row.get::<_, String>(0)?,
            depth_usdg_raw: row.get::<_, i64>(1)? as u128,
        })
    })?;
    let mut depths = Vec::new();
    for row in rows {
        let depth = row?;
        // The native zero address is a placeholder for an unknown pool
        // side, not a token. A depth row for it says nothing about any
        // real hop, so it is not evidence.
        if !is_depth_bearing_token(&depth.token_address) {
            continue;
        }
        depths.push(depth);
    }
    depths.sort_by(|left, right| left.token_address.cmp(&right.token_address));
    Ok(depths)
}

/// Renders the evidence report for the CLI.
pub fn render_report(report: &EvidenceReport) -> serde_json::Value {
    serde_json::json!({
        "measuredStockCount": report.measured_stock_count,
        "valuedStockCount": report.valued_stock_count,
        "unvaluableStockCount": report.unvaluable_stock_count,
        "unknownStockCount": report.unknown_stock_count,
        "measuredExtensionCount": report.measured_extension_count,
        "dustExtensionCount": report.dust_extension_count,
        "unmeasuredExtensionCount": report.unmeasured_extension_count,
        "usdgDecimals": USDG_DECIMALS,
        "minimumEconomicPositionUsdgRaw": EconomicPolicy::default().min_economic_position_usdg_raw,
        "minimumPoolDepthUsdgRaw": EconomicPolicy::default().min_pool_depth_usdg_raw,
        "minimumEconomicPositionUsdg": usdg_to_decimal(
            EconomicPolicy::default().min_economic_position_usdg_raw
        ),
        "minimumPoolDepthUsdg": usdg_to_decimal(
            EconomicPolicy::default().min_pool_depth_usdg_raw
        ),
    })
}

/// Builds a depth map keyed by token address, for reporting.
pub fn depth_map(depths: &[MeasuredDepth]) -> BTreeMap<String, u128> {
    depths
        .iter()
        .map(|depth| {
            (
                depth.token_address.to_ascii_lowercase(),
                depth.depth_usdg_raw,
            )
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::valuation::TokenValuation;

    fn valuation(address: &str, verdict: ValuationVerdict, amount: Option<&str>) -> TokenValuation {
        TokenValuation {
            token_address: address.to_ascii_lowercase(),
            token_symbol: Some("T".to_owned()),
            verdict,
            has_value: verdict == ValuationVerdict::RouteFound,
            input_amount_raw: "1000000000000000000".to_owned(),
            usdg_amount_raw: amount.map(|value| value.to_owned()),
            usdg_amount_decimal: amount.map(|value| value.to_owned()),
            price_impact: None,
            gas_estimate: None,
            route_leg_count: None,
            protocols: vec!["V4".to_owned()],
            failure_kind: None,
            failure_detail: None,
            source: "uniswap_trading_api_quote".to_owned(),
            observed_at: "2026-11-12T00:00:00Z".to_owned(),
        }
    }

    fn report(tokens: Vec<TokenValuation>) -> ValuationBatchReport {
        ValuationBatchReport {
            source: "uniswap_trading_api_quote".to_owned(),
            observed_at: "2026-11-12T00:00:00Z".to_owned(),
            nominal_amount: "1".to_owned(),
            protocols: vec!["V4".to_owned()],
            requested_count: tokens.len(),
            evaluated_count: tokens.len(),
            route_found_count: tokens
                .iter()
                .filter(|token| token.verdict == ValuationVerdict::RouteFound)
                .count(),
            no_route_count: tokens
                .iter()
                .filter(|token| token.verdict == ValuationVerdict::NoRoute)
                .count(),
            unknown_count: tokens
                .iter()
                .filter(|token| token.verdict.is_unknown())
                .count(),
            tokens,
        }
    }

    #[test]
    fn a_tokens_only_report_is_read_and_its_counters_are_rebuilt() {
        // The base-asset probe writes a report that carries the per-token
        // rows and nothing else.  The reader accepts that shape, because a
        // valuation that cannot be landed is a valuation that is lost.
        let directory = tempfile::tempdir().expect("tempdir");
        let path = directory.path().join("base_asset.json");
        let document = serde_json::json!({
            "tokens": [
                {
                    "token_address": "0x0000000000000000000000000000000000000000",
                    "token_symbol": "ETH",
                    "verdict": "route_found",
                    "has_value": true,
                    "input_amount_raw": "1000000000000000000",
                    "usdg_amount_raw": "2044537553",
                    "usdg_amount_decimal": "2044.537553",
                    "price_impact": "0.01",
                    "protocols": ["V4"],
                    "source": "uniswap_trading_api_quote",
                    "observed_at": "2026-11-12T05:51:13.215896200+00:00"
                },
                {
                    "token_address": "0xaaa",
                    "token_symbol": "AAA",
                    "verdict": "no_route",
                    "has_value": false,
                    "input_amount_raw": "1000000000000000000",
                    "usdg_amount_raw": null,
                    "protocols": [],
                    "source": "uniswap_trading_api_quote",
                    "observed_at": "2026-11-12T05:51:13.215896200+00:00"
                }
            ],
            "sources": ["some_other_report.json"]
        });
        std::fs::write(&path, serde_json::to_vec_pretty(&document).unwrap()).unwrap();

        let read = read_valuation_report(&path).unwrap().expect("a report");
        assert_eq!(read.tokens.len(), 2);
        // The counters are rebuilt from the rows, not taken from an
        // envelope that may describe a different population.
        assert_eq!(read.requested_count, 2);
        assert_eq!(read.route_found_count, 1);
        assert_eq!(read.no_route_count, 1);
        assert_eq!(read.unknown_count, 0);
        assert_eq!(read.source, "uniswap_trading_api_quote");
    }

    #[test]
    fn a_full_batch_report_is_still_read_from_its_embedded_report() {
        let directory = tempfile::tempdir().expect("tempdir");
        let path = directory.path().join("stock.json");
        let full = report(vec![valuation(
            "0xaaa",
            ValuationVerdict::RouteFound,
            Some("1500000"),
        )]);
        let document = serde_json::json!({
            "report": full,
            "tokens": full.tokens,
            "source": "uniswap_trading_api_quote"
        });
        std::fs::write(&path, serde_json::to_vec_pretty(&document).unwrap()).unwrap();

        let read = read_valuation_report(&path).unwrap().expect("a report");
        assert_eq!(read.tokens.len(), 1);
        assert_eq!(read.route_found_count, 1);
    }

    #[test]
    fn the_placeholder_address_is_not_a_measured_hop() {
        let path = std::env::temp_dir().join(format!(
            "usdg_evidence_placeholder_{}.sqlite3",
            std::process::id()
        ));
        let _ = std::fs::remove_file(&path);
        {
            let connection = rusqlite::Connection::open(&path).expect("database");
            connection
                .execute_batch(
                    "CREATE TABLE token_usdg_depth_measurements (
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
                     INSERT INTO token_usdg_depth_measurements
                       (network, chain_id, token_address, pool_id, depth_usdg_raw, measured_at)
                     VALUES
                       ('robinhood', 4663,
                        '0x0000000000000000000000000000000000000000',
                        '0xpool', 50000000, '2026-11-12T00:00:00Z'),
                       ('robinhood', 4663, '0xaaa', '0xpool', 50000000,
                        '2026-11-12T00:00:00Z');",
                )
                .expect("schema");
        }
        let depths = read_measured_depths(&path, crate::ROBINHOOD_NETWORK, crate::ROBINHOOD_CHAIN_ID)
            .expect("read depths");
        let _ = std::fs::remove_file(&path);
        assert_eq!(depths.len(), 1);
        assert_eq!(depths[0].token_address, "0xaaa");
    }

    #[test]
    fn the_placeholder_address_is_rejected() {
        assert!(!is_depth_bearing_token(ZERO_TOKEN_ADDRESS));
        assert!(!is_depth_bearing_token(""));
        assert!(is_depth_bearing_token("0xaaa"));
    }

    #[test]
    fn a_live_route_is_recorded_as_a_measured_valuation() {
        let report = report(vec![valuation(
            "0xAA",
            ValuationVerdict::RouteFound,
            Some("150000000"),
        )]);
        let evidence = build_evidence(Some(&report), &[]);
        assert_eq!(evidence.measured_stock_count, 1);
        assert_eq!(evidence.valued_stock_count, 1);
        assert_eq!(evidence.unvaluable_stock_count, 0);
        assert_eq!(evidence.unknown_stock_count, 0);
        assert_eq!(
            evidence.evidence.stock_values_usdg_raw.get("0xaa"),
            Some(&150_000_000u128)
        );
        assert!(evidence.evidence.measured_stock_addresses.contains("0xaa"));
    }

    #[test]
    fn no_route_is_a_genuine_negative() {
        let report = report(vec![valuation("0xBB", ValuationVerdict::NoRoute, None)]);
        let evidence = build_evidence(Some(&report), &[]);
        assert_eq!(evidence.measured_stock_count, 1);
        assert_eq!(evidence.unvaluable_stock_count, 1);
        assert_eq!(evidence.unknown_stock_count, 0);
        assert!(evidence.evidence.measured_stock_addresses.contains("0xbb"));
        assert!(!evidence.evidence.stock_values_usdg_raw.contains_key("0xbb"));
    }

    #[test]
    fn an_api_failure_is_unknown_and_never_a_negative() {
        let report = report(vec![valuation(
            "0xCC",
            ValuationVerdict::ApiUnavailable,
            None,
        )]);
        let evidence = build_evidence(Some(&report), &[]);
        assert_eq!(evidence.measured_stock_count, 0);
        assert_eq!(evidence.unvaluable_stock_count, 0);
        assert_eq!(evidence.unknown_stock_count, 1);
        // Not measured: the gate must treat it as pending, not excluded.
        assert!(!evidence.evidence.measured_stock_addresses.contains("0xcc"));
    }

    #[test]
    fn the_measured_depths_become_the_extension_evidence() {
        let depths = vec![MeasuredDepth {
            token_address: "0xDD".to_owned(),
            depth_usdg_raw: 50_000_000,
        }];
        let evidence = build_evidence(None, &depths);
        assert_eq!(evidence.measured_extension_count, 1);
        assert_eq!(evidence.dust_extension_count, 0);
        assert_eq!(
            evidence.evidence.extension_depth_usdg_raw.get("0xdd"),
            Some(&50_000_000u128)
        );
    }

    #[test]
    fn a_rendered_cli_report_round_trips_back_into_the_evidence() {
        let report = report(vec![valuation(
            "0xAA",
            ValuationVerdict::RouteFound,
            Some("150000000"),
        )]);
        let rendered = serde_json::json!({
            "report": &report,
            "routeFoundCount": report.route_found_count,
            "tokens": report.tokens,
        });
        let document = rendered.to_string();
        let parsed: serde_json::Value = serde_json::from_str(&document).expect("valid json");
        let embedded = parsed.get("report").expect("embedded report");
        let restored: ValuationBatchReport =
            serde_json::from_value(embedded.clone()).expect("round trip");
        let evidence = build_evidence(Some(&restored), &[]);
        assert_eq!(evidence.measured_stock_count, 1);
        assert_eq!(evidence.valued_stock_count, 1);
        assert_eq!(
            evidence.evidence.stock_values_usdg_raw.get("0xaa"),
            Some(&150_000_000u128)
        );
    }

    #[test]
    fn a_shallow_hop_is_counted_as_dust() {
        let depths = vec![MeasuredDepth {
            token_address: "0xEE".to_owned(),
            depth_usdg_raw: 1,
        }];
        let evidence = build_evidence(None, &depths);
        assert_eq!(evidence.measured_extension_count, 1);
        assert_eq!(evidence.dust_extension_count, 1);
    }
}
