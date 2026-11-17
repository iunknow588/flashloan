//! CLI surface for the coarse USDG valuation probe.
//!
//! The command is read-only: it queries the hosted Uniswap Trading API and
//! prints a JSON report. It never writes to the production database and it
//! never participates in the depth, capacity or broadcast gates.

use crate::{
    configured_api_key, read_stock_registry, value_tokens_to_usdg_batch_with_concurrency,
    ValuationConfig, ValuationTarget, CANONICAL_USDG_ADDRESS, ROBINHOOD_CHAIN_ID,
    ROBINHOOD_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_NETWORK,
    UNISWAP_TRADING_API_QUOTE_URL,
};
use serde_json::json;
use std::error::Error;
use std::path::PathBuf;

/// Default nominal input amount used for the probe, in whole token units.
const DEFAULT_NOMINAL_AMOUNT: &str = "1";

/// Default protocols allowed for routing.
const DEFAULT_PROTOCOLS: &str = "V4";

/// Runs the `value-tokens` subcommand.
///
/// Supported flags:
///
/// * `--registry <path>`: stock registry JSON; when omitted the tool probes
///   the tokens given by `--token`.
/// * `--token <address|symbol>`: probe one token instead of a registry.
/// * `--token-symbol <symbol>`: symbol for `--token` when it is an address.
/// * `--token-decimals <n>`: decimals for `--token` when it is an address.
/// * `--amount <decimal>`: nominal input amount, default `1`.
/// * `--protocols <csv>`: default `V4`.
/// * `--limit <n>`: cap the number of probes.
/// * `--concurrency <n>`: number of concurrent requests, default 8.
/// * `--quote-url <url>`: override the Trading API endpoint.
/// * `--no-api-key`: force the unavailable path, for offline verification.
pub fn run(arguments: &[String]) -> Result<String, Box<dyn Error>> {
    let config = build_config(arguments)?;
    let nominal_amount = flag(arguments, "--amount").unwrap_or(DEFAULT_NOMINAL_AMOUNT);
    let limit = flag(arguments, "--limit").and_then(|value| value.parse::<usize>().ok());

    let mut targets = Vec::new();
    if let Some(token) = flag(arguments, "--token") {
        let address = resolve_token_address(arguments, token)?;
        let decimals = flag(arguments, "--token-decimals")
            .and_then(|value| value.parse::<u8>().ok())
            .unwrap_or(18);
        let symbol = flag(arguments, "--token-symbol").map(|value| value.to_owned());
        targets.push(ValuationTarget {
            token_address: address,
            token_symbol: symbol,
            token_decimals: decimals,
        });
    } else if let Some(registry) = flag(arguments, "--registry") {
        let scope = chain_scope(arguments)?;
        let records = read_stock_registry(
            PathBuf::from(registry),
            scope.0,
            scope.1,
        )?;
        for record in records {
            let Some(address) = record.token_address.clone() else {
                continue;
            };
            targets.push(ValuationTarget {
                token_address: address,
                token_symbol: record.token_symbol.clone(),
                token_decimals: record.token_decimals.unwrap_or(18).clamp(0, 36) as u8,
            });
        }
    } else {
        return Err(
            "value-tokens requires --registry <stock registry JSON> or --token <address>".into(),
        );
    }

    if let Some(limit) = limit {
        targets.truncate(limit);
    }

    let concurrency = flag(arguments, "--concurrency")
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(crate::valuation::DEFAULT_CONCURRENCY)
        .max(1);
    let report =
        value_tokens_to_usdg_batch_with_concurrency(&targets, nominal_amount, &config, concurrency);
    let verdict_counts = report.verdict_counts();
    // The report is embedded under `report` so that the very same JSON
    // document can be read back by `materialize-libraries` as the
    // valuation evidence. The flat fields stay for human reading.
    let rendered = json!({
        "report": &report,
        "source": report.source,
        "observedAt": report.observed_at,
        "quoteTarget": CANONICAL_USDG_ADDRESS,
        "quoteEndpoint": config.quote_url,
        "chainId": config.chain_id,
        "nominalAmount": report.nominal_amount,
        "protocols": report.protocols,
        "apiKeyConfigured": configured_api_key().is_some(),
        "requestedCount": report.requested_count,
        "evaluatedCount": report.evaluated_count,
        "routeFoundCount": report.route_found_count,
        "noRouteCount": report.no_route_count,
        "unknownCount": report.unknown_count,
        "verdictCounts": verdict_counts,
        "tokens": report.tokens,
    });
    Ok(serde_json::to_string_pretty(&rendered)?)
}

/// Builds the valuation configuration from the command line.
fn build_config(arguments: &[String]) -> Result<ValuationConfig, Box<dyn Error>> {
    let mut config = ValuationConfig::default();
    // The quote request carries the chain id of the token being priced, so
    // the config has to describe the same scope the registry is read from.
    let (_, chain_id) = chain_scope(arguments)?;
    config = config.with_chain_id(chain_id);
    if let Some(url) = flag(arguments, "--quote-url") {
        config = config.with_quote_url(url);
    }
    if arguments.iter().any(|argument| argument == "--no-api-key") {
        config = config.with_api_key(None);
    }
    if let Some(protocols) = flag(arguments, "--protocols") {
        let parsed = protocols
            .split(',')
            .map(|value| value.trim().to_ascii_uppercase())
            .filter(|value| !value.is_empty())
            .collect::<Vec<_>>();
        if parsed.is_empty() {
            return Err("--protocols must list at least one protocol".into());
        }
        config = config.with_protocols(parsed);
    } else {
        config = config.with_protocols(vec![DEFAULT_PROTOCOLS.to_owned()]);
    }
    Ok(config)
}

/// Resolves a token argument to a lowercase address.
///
/// A 20-byte hex value is treated as an address. A symbol is resolved
/// against the stock registry when one is supplied; otherwise the argument
/// is rejected, because guessing an address from a symbol is unsafe.
/// Resolves the chain scope a valuation run reads its registry from.
///
/// The stock registry carries its own network and chain id, so the scope
/// has to be the one the registry was written for. It is taken from the
/// explicit flags when present and otherwise from the registry itself.
fn chain_scope(arguments: &[String]) -> Result<(&'static str, u64), Box<dyn Error>> {
    if let Some(network) = flag(arguments, "--network") {
        return match network {
            ROBINHOOD_NETWORK => Ok((ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID)),
            ROBINHOOD_TESTNET_NETWORK => Ok((ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID)),
            other => Err(format!(
                "unsupported network: {other}; expected {ROBINHOOD_NETWORK} or {ROBINHOOD_TESTNET_NETWORK}"
            )
            .into()),
        };
    }
    if let Some(chain_id) = flag(arguments, "--chain-id") {
        let chain_id = chain_id
            .parse::<u64>()
            .map_err(|error| format!("invalid --chain-id: {error}"))?;
        return match chain_id {
            ROBINHOOD_CHAIN_ID => Ok((ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID)),
            ROBINHOOD_TESTNET_CHAIN_ID => {
                Ok((ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID))
            }
            other => Err(format!(
                "unsupported chain-id: {other}; expected {ROBINHOOD_CHAIN_ID} or {ROBINHOOD_TESTNET_CHAIN_ID}"
            )
            .into()),
        };
    }
    // Without an explicit scope the registry file states its own.
    if let Some(registry) = flag(arguments, "--registry") {
        let payload = std::fs::read(registry)?;
        let value: serde_json::Value = serde_json::from_slice(&payload)?;
        let network = value.get("network").and_then(serde_json::Value::as_str);
        let chain_id = value.get("chain_id").and_then(serde_json::Value::as_u64);
        match (network, chain_id) {
            (Some(ROBINHOOD_NETWORK), Some(ROBINHOOD_CHAIN_ID)) => {
                Ok((ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID))
            }
            (Some(ROBINHOOD_TESTNET_NETWORK), Some(ROBINHOOD_TESTNET_CHAIN_ID)) => {
                Ok((ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID))
            }
            _ => Err("the stock registry does not state a supported network and chain id".into()),
        }
    } else {
        Ok((ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID))
    }
}

fn resolve_token_address(arguments: &[String], token: &str) -> Result<String, Box<dyn Error>> {
    let trimmed = token.trim();
    if is_address(trimmed) {
        return Ok(trimmed.to_ascii_lowercase());
    }
    if let Some(registry) = flag(arguments, "--registry") {
        let scope = chain_scope(arguments)?;
        let records = read_stock_registry(
            PathBuf::from(registry),
            scope.0,
            scope.1,
        )?;
        let upper = trimmed.to_ascii_uppercase();
        for record in records {
            let symbol_matches = record
                .token_symbol
                .as_deref()
                .map(|value| value.trim().eq_ignore_ascii_case(&upper))
                .unwrap_or(false);
            if symbol_matches {
                if let Some(address) = record.token_address {
                    return Ok(address.to_ascii_lowercase());
                }
            }
        }
        return Err(format!("token symbol {trimmed:?} was not found in the registry").into());
    }
    Err(
        format!("token {trimmed:?} is not an address; supply --registry to resolve a symbol")
            .into(),
    )
}

/// Whether the value looks like a 20-byte hex address.
fn is_address(value: &str) -> bool {
    let body = value.strip_prefix("0x").unwrap_or(value);
    body.len() == 40 && body.chars().all(|character| character.is_ascii_hexdigit())
}

/// Reads a flag value.
fn flag<'a>(arguments: &'a [String], name: &str) -> Option<&'a str> {
    arguments
        .windows(2)
        .find(|pair| pair[0] == name)
        .map(|pair| pair[1].as_str())
}

/// The default endpoint, exposed so the CLI can report it.
pub const DEFAULT_QUOTE_ENDPOINT: &str = UNISWAP_TRADING_API_QUOTE_URL;
