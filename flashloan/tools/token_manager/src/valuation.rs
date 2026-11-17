//! Coarse token valuation against the canonical USDG asset.
//!
//! The token manager needs to answer one question for capital tokens (most
//! importantly the stock tokens): *does this token have a live exchange path
//! to USDG, and roughly how much is one unit worth?*
//!
//! The answer is produced by the Uniswap Trading API `/quote` endpoint, a
//! hosted off-chain routing service. The service owns the pool index and the
//! routing algorithm, so the tool does not need a local V4 SDK, a local pool
//! search, or a Robinhood Chain RPC round trip.
//!
//! Scope boundaries (deliberate):
//!
//! * This is a **valuation / observation** capability. It never feeds the
//!   depth gate, the capacity gate or the broadcast path, and it never
//!   introduces an external USD price feed into execution decisions.
//! * Exact pool details are intentionally *not* modelled. Only the number of
//!   route legs is reported, because the caller explicitly does not care how
//!   many hops a route uses.
//! * Absence of a quote is never reported as "the token has no value".
//!   The verdict enum keeps `no_route` (a real negative) separate from
//!   `api_unavailable` / `api_error` (unknown), so a missing API key or an
//!   upstream outage can never be misread as a worthless token.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, OnceLock};
use std::time::Duration;

/// Hosted Uniswap Trading API quote endpoint.
pub const UNISWAP_TRADING_API_QUOTE_URL: &str = "https://trade-api.gateway.uniswap.org/v1/quote";

/// Canonical USDG on Robinhood Chain.
///
/// Matching is by address, never by symbol: three other live ERC-20s on the
/// same chain advertise the `USDG` symbol and must never be used as the
/// quote target.
pub const CANONICAL_USDG_ADDRESS: &str = "0x5fc5360d0400a0fd4f2af552add042d716f1d168";

/// USDG uses 6 decimals on this chain.
pub const CANONICAL_USDG_DECIMALS: u8 = 6;

/// Environment variables consulted for the Trading API key, in priority order.
pub const TRADING_API_KEY_ENV_VARS: &[&str] = &[
    "UNISWAP_TRADING_API_KEY",
    "UNISWAP_API_KEY",
    "UNISWAP_TRADING_KEY",
];

/// Default per-request timeout in seconds.
pub const DEFAULT_REQUEST_TIMEOUT_SECONDS: u64 = 20;

/// Default number of retries for transport errors and HTTP 429.
pub const DEFAULT_RETRIES: u32 = 2;

/// Default number of concurrent valuation requests.
///
/// The batch is large enough (two hundred stock tokens) that a strictly
/// sequential run against an unreachable endpoint would take hours. A small
/// pool bounds that worst case without turning the probe into a load test
/// against a shared hosted service.
pub const DEFAULT_CONCURRENCY: usize = 8;

/// Number of consecutive transport failures that opens the circuit.
///
/// The breaker exists so that an unreachable endpoint costs one timeout
/// window per worker instead of one per token.
pub const CIRCUIT_BREAKER_THRESHOLD: usize = 3;

/// Default base backoff between retries, in milliseconds.
pub const DEFAULT_RETRY_BACKOFF_MS: u64 = 500;

/// Default number of worker threads for the embedded async runtime.
const RUNTIME_WORKER_THREADS: usize = 4;

/// Identifier recorded in every observation.
pub const SOURCE_ID: &str = "uniswap_trading_api_quote";

/// The verdict of one valuation probe.
///
/// The variants are ordered by how much they may be trusted for a decision:
/// only [`ValuationVerdict::RouteFound`] proves a live path, only
/// [`ValuationVerdict::NoRoute`] is a real negative, and everything else is
/// explicitly unknown.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ValuationVerdict {
    /// The service returned a usable quote: a route to USDG exists.
    RouteFound,
    /// The service answered and explicitly reported that no route exists.
    NoRoute,
    /// No API key is configured, so the probe could not be attempted.
    ApiUnavailable,
    /// The request failed (transport, timeout, HTTP status, malformed body).
    ApiError,
}

impl ValuationVerdict {
    /// Whether this verdict proves that a token has a live USDG path.
    pub fn proves_route(&self) -> bool {
        matches!(self, ValuationVerdict::RouteFound)
    }

    /// Whether this verdict is a genuine "no value" negative.
    ///
    /// An unknown verdict must never be reported as a negative.
    pub fn is_negative(&self) -> bool {
        matches!(self, ValuationVerdict::NoRoute)
    }

    /// Whether the verdict leaves the token value unknown.
    pub fn is_unknown(&self) -> bool {
        matches!(
            self,
            ValuationVerdict::ApiUnavailable | ValuationVerdict::ApiError
        )
    }

    /// Stable machine-readable token for the verdict.
    pub fn as_str(&self) -> &'static str {
        match self {
            ValuationVerdict::RouteFound => "route_found",
            ValuationVerdict::NoRoute => "no_route",
            ValuationVerdict::ApiUnavailable => "api_unavailable",
            ValuationVerdict::ApiError => "api_error",
        }
    }
}

impl fmt::Display for ValuationVerdict {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

/// Why a probe did not produce a route, when it did not.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ValuationFailureKind {
    /// No API key was found in the environment.
    MissingApiKey,
    /// The service returned HTTP 401/403.
    Unauthorized,
    /// The service returned HTTP 429.
    RateLimited,
    /// The service returned a non-success HTTP status.
    HttpStatus,
    /// The request did not complete (transport error or timeout).
    Transport,
    /// The response body was not valid JSON, or did not match the expected
    /// shape. A malformed body is an unknown, never a negative.
    MalformedResponse,
    /// The service explicitly reported that no route exists.
    NoRoute,
}

impl ValuationFailureKind {
    /// Stable machine-readable token for the failure.
    pub fn as_str(&self) -> &'static str {
        match self {
            ValuationFailureKind::MissingApiKey => "missing_api_key",
            ValuationFailureKind::Unauthorized => "unauthorized",
            ValuationFailureKind::RateLimited => "rate_limited",
            ValuationFailureKind::HttpStatus => "http_status",
            ValuationFailureKind::Transport => "transport",
            ValuationFailureKind::MalformedResponse => "malformed_response",
            ValuationFailureKind::NoRoute => "no_route",
        }
    }
}

/// One token's valuation result.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TokenValuation {
    /// Token address, lowercase.
    pub token_address: String,
    /// Token symbol when known.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub token_symbol: Option<String>,
    /// The verdict of the probe.
    pub verdict: ValuationVerdict,
    /// `true` only when a live route to USDG was observed.
    pub has_value: bool,
    /// Input amount used for the probe, in raw token units.
    pub input_amount_raw: String,
    /// Expected USDG output in raw (6-decimal) units.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub usdg_amount_raw: Option<String>,
    /// Expected USDG output rendered as a decimal string.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub usdg_amount_decimal: Option<String>,
    /// Price impact reported by the routing service, if present.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub price_impact: Option<String>,
    /// Gas estimate reported by the routing service, if present.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gas_estimate: Option<String>,
    /// Number of route legs. The route internals are intentionally dropped.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub route_leg_count: Option<usize>,
    /// Protocols the routing service used.
    pub protocols: Vec<String>,
    /// Failure classification when the verdict is not `route_found`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub failure_kind: Option<ValuationFailureKind>,
    /// Human-readable detail for a failure. Never contains the API key.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub failure_detail: Option<String>,
    /// Source of the observation.
    pub source: String,
    /// RFC3339 timestamp of the observation.
    pub observed_at: String,
}

impl TokenValuation {
    pub(crate) fn base(
        token_address: &str,
        token_symbol: Option<&str>,
        input_amount_raw: &str,
        protocols: &[String],
    ) -> Self {
        Self {
            token_address: token_address.to_ascii_lowercase(),
            token_symbol: token_symbol.map(|value| value.to_owned()),
            verdict: ValuationVerdict::ApiError,
            has_value: false,
            input_amount_raw: input_amount_raw.to_owned(),
            usdg_amount_raw: None,
            usdg_amount_decimal: None,
            price_impact: None,
            gas_estimate: None,
            route_leg_count: None,
            protocols: protocols.to_vec(),
            failure_kind: None,
            failure_detail: None,
            source: SOURCE_ID.to_owned(),
            observed_at: now_rfc3339(),
        }
    }
}

/// Configuration for one valuation probe.
#[derive(Clone, Debug)]
pub struct ValuationConfig {
    /// Base URL of the Trading API quote endpoint.
    pub quote_url: String,
    /// API key. `None` produces an `api_unavailable` verdict.
    pub api_key: Option<String>,
    /// Chain id used for both sides of the quote.
    pub chain_id: u64,
    /// Quote target. Always the canonical USDG address.
    pub token_out: String,
    /// Protocols to allow the router to use.
    pub protocols: Vec<String>,
    /// Slippage tolerance in percent, as accepted by the Trading API.
    pub slippage_tolerance: f64,
    /// Routing preference handed to the service.
    pub routing_preference: String,
    /// `swapper` address sent with the request. The router treats it as the
    /// account that would perform the swap; a placeholder is accepted for a
    /// read-only quote.
    pub swapper: String,
    /// Per-request timeout in seconds.
    pub timeout_seconds: u64,
    /// Retry budget for transport failures and HTTP 429.
    pub retries: u32,
}

impl Default for ValuationConfig {
    fn default() -> Self {
        Self {
            quote_url: UNISWAP_TRADING_API_QUOTE_URL.to_owned(),
            api_key: configured_api_key(),
            chain_id: crate::sources::ROBINHOOD_CHAIN_ID,
            token_out: CANONICAL_USDG_ADDRESS.to_owned(),
            protocols: vec!["V4".to_owned()],
            slippage_tolerance: 0.5,
            routing_preference: "BEST_PRICE".to_owned(),
            swapper: "0x0000000000000000000000000000000000000001".to_owned(),
            timeout_seconds: DEFAULT_REQUEST_TIMEOUT_SECONDS,
            retries: DEFAULT_RETRIES,
        }
    }
}

impl ValuationConfig {
    /// Builds a configuration from the environment, if an API key is present.
    pub fn from_env() -> Self {
        Self::default()
    }

    /// Overrides the quote endpoint (used by tests and by private gateways).
    pub fn with_quote_url(mut self, quote_url: impl Into<String>) -> Self {
        self.quote_url = quote_url.into();
        self
    }

    /// Overrides the API key.
    pub fn with_api_key(mut self, api_key: Option<String>) -> Self {
        self.api_key = api_key;
        self
    }

    /// Overrides the allowed protocols.
    pub fn with_protocols(mut self, protocols: Vec<String>) -> Self {
        self.protocols = protocols;
        self
    }

    /// Overrides the chain id.
    pub fn with_chain_id(mut self, chain_id: u64) -> Self {
        self.chain_id = chain_id;
        self
    }
}

/// Reads the Trading API key from the environment.
///
/// The key is never logged and never returned in a report.
pub fn configured_api_key() -> Option<String> {
    for name in TRADING_API_KEY_ENV_VARS {
        if let Ok(value) = std::env::var(name) {
            let trimmed = value.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_owned());
            }
        }
    }
    None
}

/// Builds the `/quote` request body for a token-to-USDG probe.
///
/// The body is pure JSON so the exact wire shape can be asserted offline.
pub fn build_quote_request(token_in: &str, amount_raw: &str, config: &ValuationConfig) -> Value {
    json!({
        "type": "EXACT_INPUT",
        "amount": amount_raw,
        "tokenInChainId": config.chain_id,
        "tokenOutChainId": config.chain_id,
        "tokenIn": token_in,
        "tokenOut": config.token_out,
        "swapper": config.swapper,
        "protocols": config.protocols,
        "slippageTolerance": config.slippage_tolerance,
        "routingPreference": config.routing_preference,
    })
}

/// Parses a `/quote` response body into a valuation.
///
/// The parser is deliberately tolerant: a body that cannot be understood
/// yields `api_error` with `malformed_response`, never `no_route`.
pub fn parse_quote_response(
    body: &Value,
    token_address: &str,
    token_symbol: Option<&str>,
    input_amount_raw: &str,
    protocols: &[String],
) -> TokenValuation {
    let mut valuation =
        TokenValuation::base(token_address, token_symbol, input_amount_raw, protocols);
    match body {
        Value::Object(object) => {
            if let Some(detail) = explicit_no_route_detail(object) {
                valuation.verdict = ValuationVerdict::NoRoute;
                valuation.failure_kind = Some(ValuationFailureKind::NoRoute);
                valuation.failure_detail = Some(detail);
                return valuation;
            }
            let quote = object.get("quote").and_then(Value::as_object);
            let output = quote
                .and_then(|quote| quote.get("output"))
                .and_then(Value::as_object);
            let amount = output
                .and_then(|output| output.get("amount"))
                .and_then(json_as_decimal_string);
            match amount {
                Some(amount) => {
                    valuation.verdict = ValuationVerdict::RouteFound;
                    valuation.has_value = true;
                    valuation.usdg_amount_raw = Some(amount.clone());
                    valuation.usdg_amount_decimal =
                        Some(raw_to_decimal(&amount, CANONICAL_USDG_DECIMALS));
                    valuation.price_impact = quote
                        .and_then(|quote| quote.get("priceImpact"))
                        .and_then(json_as_decimal_string);
                    valuation.gas_estimate = quote
                        .and_then(|quote| quote.get("gasEstimate"))
                        .and_then(json_as_decimal_string);
                    valuation.route_leg_count = count_route_legs(object);
                }
                None => {
                    valuation.verdict = ValuationVerdict::ApiError;
                    valuation.failure_kind = Some(ValuationFailureKind::MalformedResponse);
                    valuation.failure_detail =
                        Some("quote response did not contain quote.output.amount".to_owned());
                }
            }
        }
        _ => {
            valuation.verdict = ValuationVerdict::ApiError;
            valuation.failure_kind = Some(ValuationFailureKind::MalformedResponse);
            valuation.failure_detail = Some("quote response was not a JSON object".to_owned());
        }
    }
    valuation
}

/// Detects an explicit "no route" answer from the service.
fn explicit_no_route_detail(object: &serde_json::Map<String, Value>) -> Option<String> {
    for key in ["errorCode", "error", "detail", "message", "reason"] {
        let Some(value) = object.get(key) else {
            continue;
        };
        let Some(text) = value.as_str() else {
            continue;
        };
        if is_explicit_no_route_text(text) {
            return Some(text.to_owned());
        }
    }
    None
}

/// Whether a service-supplied string explicitly says "there is no route".
///
/// The Trading API uses `NoRouteFoundError` as its `errorCode`, which is
/// camelCase and therefore invisible to a plain "no route" substring test.
/// The separators are stripped before matching so one rule covers
/// `NoRouteFoundError`, `no_route_found`, `NO ROUTE` and `NoRoute`.
fn is_explicit_no_route_text(text: &str) -> bool {
    let lowered = text.to_ascii_lowercase();
    let collapsed: String = lowered
        .chars()
        .filter(|character| character.is_ascii_alphanumeric())
        .collect();
    if collapsed.contains("noroute")
        || collapsed.contains("nopath")
        || collapsed.contains("notroutable")
        || collapsed.contains("noquote")
    {
        return true;
    }
    lowered.contains("no route")
        || lowered.contains("no_route")
        || lowered.contains("no path")
        || lowered.contains("not routable")
        || lowered.contains("no quote")
        || lowered.contains("unsupported token")
}

/// Detects an explicit "no route" answer inside a non-success response body.
///
/// The Trading API answers a routeless token with HTTP 404 and a JSON body
/// such as `{"errorCode":"NoRouteFoundError"}`. The keyword set is the very
/// same one used by [`explicit_no_route_detail`] so the two paths can never
/// drift apart: there is exactly one definition of "the service said there
/// is no route".
///
/// A body that is not JSON, or that carries no explicit no-route signal,
/// yields `None`; the caller then keeps the conservative `api_error`
/// classification.
pub(crate) fn no_route_detail_from_body(body: &str) -> Option<String> {
    let trimmed = body.trim();
    if trimmed.is_empty() {
        return None;
    }
    let parsed: Value = serde_json::from_str(trimmed).ok()?;
    let object = parsed.as_object()?;
    explicit_no_route_detail(object)
}

/// Counts route legs without retaining any pool detail.
fn count_route_legs(object: &serde_json::Map<String, Value>) -> Option<usize> {
    let route = object.get("route")?;
    if let Some(items) = route.as_array() {
        return Some(items.len());
    }
    if let Some(inner) = route.get("legs") {
        if let Some(items) = inner.as_array() {
            return Some(items.len());
        }
        if let Some(items) = inner.get("path").and_then(Value::as_array) {
            return Some(items.len());
        }
    }
    None
}

/// Reads a JSON value that may be a string or a number as a decimal string.
fn json_as_decimal_string(value: &Value) -> Option<String> {
    match value {
        Value::String(text) => Some(text.trim().to_owned()),
        Value::Number(number) => Some(number.to_string()),
        _ => None,
    }
}

/// Renders a raw integer amount as a decimal string.
///
/// Integer-only arithmetic is used throughout so no floating point rounding
/// can corrupt a thin-pool quote.
pub fn raw_to_decimal(raw: &str, decimals: u8) -> String {
    let trimmed = raw.trim();
    let (negative, digits) = match trimmed.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, trimmed),
    };
    let normalized = digits.trim_start_matches('0');
    if normalized.is_empty() {
        return "0".to_owned();
    }
    if decimals == 0 {
        return format!("{}{}", if negative { "-" } else { "" }, normalized);
    }
    let decimals = usize::from(decimals);
    let padded = if normalized.len() <= decimals {
        format!(
            "{}{}",
            "0".repeat(decimals + 1 - normalized.len()),
            normalized
        )
    } else {
        normalized.to_owned()
    };
    let split = padded.len() - decimals;
    let whole = &padded[..split];
    let fraction = padded[split..].trim_end_matches('0');
    let sign = if negative { "-" } else { "" };
    if fraction.is_empty() {
        format!("{sign}{whole}")
    } else {
        format!("{sign}{whole}.{fraction}")
    }
}

/// Builds the raw input amount for a nominal token amount.
///
/// `amount_decimal` is a plain decimal string such as `"1"` or `"0.5"`.
pub fn raw_amount_from_decimal(amount_decimal: &str, token_decimals: u8) -> Option<String> {
    let trimmed = amount_decimal.trim();
    if trimmed.is_empty() {
        return None;
    }
    let (negative, body) = match trimmed.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, trimmed),
    };
    if negative {
        return None;
    }
    let (whole, fraction) = match body.split_once('.') {
        Some((whole, fraction)) => (whole, fraction),
        None => (body, ""),
    };
    if whole.is_empty() && fraction.is_empty() {
        return None;
    }
    if !whole.chars().all(|character| character.is_ascii_digit())
        || !fraction.chars().all(|character| character.is_ascii_digit())
    {
        return None;
    }
    let decimals = usize::from(token_decimals);
    let mut digits = String::new();
    digits.push_str(if whole.is_empty() { "0" } else { whole });
    if decimals > 0 {
        digits.push_str(fraction);
        for _ in fraction.len()..decimals {
            digits.push('0');
        }
    } else if !fraction.is_empty() {
        return None;
    }
    let trimmed = digits.trim_start_matches('0');
    if trimmed.is_empty() {
        Some("0".to_owned())
    } else {
        Some(trimmed.to_owned())
    }
}

/// Quotes one token against USDG.
///
/// The function never returns an error: every failure mode is encoded in the
/// verdict so a caller can distinguish "no route" from "could not ask".
pub fn quote_token_to_usdg(
    token_address: &str,
    token_symbol: Option<&str>,
    token_decimals: u8,
    amount_decimal: &str,
    config: &ValuationConfig,
) -> TokenValuation {
    let raw_amount = match raw_amount_from_decimal(amount_decimal, token_decimals) {
        Some(raw) => raw,
        None => {
            let mut valuation =
                TokenValuation::base(token_address, token_symbol, "0", &config.protocols);
            valuation.verdict = ValuationVerdict::ApiError;
            valuation.failure_kind = Some(ValuationFailureKind::MalformedResponse);
            valuation.failure_detail = Some(format!("invalid nominal amount {amount_decimal:?}"));
            return valuation;
        }
    };
    if config
        .api_key
        .as_deref()
        .map(str::trim)
        .unwrap_or("")
        .is_empty()
    {
        let mut valuation =
            TokenValuation::base(token_address, token_symbol, &raw_amount, &config.protocols);
        valuation.verdict = ValuationVerdict::ApiUnavailable;
        valuation.failure_kind = Some(ValuationFailureKind::MissingApiKey);
        valuation.failure_detail = Some(format!(
            "no Trading API key configured; set one of {}",
            TRADING_API_KEY_ENV_VARS.join(", ")
        ));
        return valuation;
    }

    let body = build_quote_request(token_address, &raw_amount, config);
    let payload = serde_json::to_string(&body).unwrap_or_else(|_| "{}".to_owned());
    let api_key = config.api_key.clone().unwrap_or_default();

    match execute_quote_request(&config.quote_url, &api_key, &payload, config) {
        QuoteOutcome::Body(value) => parse_quote_response(
            &value,
            token_address,
            token_symbol,
            &raw_amount,
            &config.protocols,
        ),
        QuoteOutcome::NoRoute(detail) => {
            let mut valuation =
                TokenValuation::base(token_address, token_symbol, &raw_amount, &config.protocols);
            valuation.verdict = ValuationVerdict::NoRoute;
            valuation.failure_kind = Some(ValuationFailureKind::NoRoute);
            valuation.failure_detail = Some(detail);
            valuation
        }
        QuoteOutcome::Failure(kind, detail) => {
            let mut valuation =
                TokenValuation::base(token_address, token_symbol, &raw_amount, &config.protocols);
            valuation.verdict = ValuationVerdict::ApiError;
            valuation.failure_kind = Some(kind);
            valuation.failure_detail = Some(detail);
            valuation
        }
    }
}

/// Outcome of the HTTP round trip.
enum QuoteOutcome {
    Body(Value),
    /// The service answered with a non-success HTTP status but explicitly
    /// reported that no route exists.
    ///
    /// The Trading API answers a routeless token with HTTP 404 and a body
    /// such as `{"errorCode":"NoRouteFoundError"}`. That is a *real
    /// negative*, not a transport problem, so it must never be reported as
    /// `api_error`.
    NoRoute(String),
    Failure(ValuationFailureKind, String),
}

/// Performs the HTTP round trip with a bounded retry budget.
fn execute_quote_request(
    url: &str,
    api_key: &str,
    payload: &str,
    config: &ValuationConfig,
) -> QuoteOutcome {
    let runtime = match quote_runtime() {
        Ok(runtime) => runtime,
        Err(error) => return QuoteOutcome::Failure(ValuationFailureKind::Transport, error),
    };
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(config.timeout_seconds.max(1)))
        .build()
    {
        Ok(client) => client,
        Err(error) => {
            return QuoteOutcome::Failure(
                ValuationFailureKind::Transport,
                format!("HTTP client failed: {error}"),
            );
        }
    };

    runtime.block_on(async {
        let mut attempt = 0_u32;
        loop {
            let response = client
                .post(url)
                .header("x-api-key", api_key)
                .header("accept", "application/json")
                .header("content-type", "application/json")
                .body(payload.to_owned())
                .send()
                .await;

            match response {
                Ok(response) => {
                    let status = response.status();
                    if status.is_success() {
                        match response.json::<Value>().await {
                            Ok(value) => return QuoteOutcome::Body(value),
                            Err(error) => {
                                return QuoteOutcome::Failure(
                                    ValuationFailureKind::MalformedResponse,
                                    format!("response was not valid JSON: {error}"),
                                );
                            }
                        }
                    }
                    // A non-success status does not by itself make the
                    // answer unknown. The Trading API answers a routeless
                    // token with HTTP 404 and a body such as
                    // `{"errorCode":"NoRouteFoundError"}`. That body is a
                    // *real negative*, so it must be read before the status
                    // is classified. The body is consumed here, so the
                    // retry path must not attempt to read it twice.
                    let status_code = status.as_u16();
                    let body = response.text().await.unwrap_or_default();
                    if let Some(detail) = no_route_detail_from_body(&body) {
                        return QuoteOutcome::NoRoute(detail);
                    }
                    let retryable = status.as_u16() == 429;
                    let detail = format!("Trading API returned HTTP {status_code}");
                    if retryable && attempt < config.retries {
                        sleep_backoff(attempt);
                        attempt += 1;
                        continue;
                    }
                    let kind = if status_code == 401 || status_code == 403 {
                        ValuationFailureKind::Unauthorized
                    } else if status_code == 429 {
                        ValuationFailureKind::RateLimited
                    } else {
                        ValuationFailureKind::HttpStatus
                    };
                    return QuoteOutcome::Failure(kind, detail);
                }
                Err(error) => {
                    let retryable = error.is_timeout() || error.is_connect();
                    if retryable && attempt < config.retries {
                        sleep_backoff(attempt);
                        attempt += 1;
                        continue;
                    }
                    return QuoteOutcome::Failure(
                        ValuationFailureKind::Transport,
                        format!("Trading API request failed: {error}"),
                    );
                }
            }
        }
    })
}

/// Sleeps between retries using an exponential backoff.
fn sleep_backoff(attempt: u32) {
    let delay = DEFAULT_RETRY_BACKOFF_MS.saturating_mul(1_u64 << attempt.min(4));
    std::thread::sleep(Duration::from_millis(delay));
}

/// Returns the shared async runtime used for HTTP calls.
fn quote_runtime() -> Result<&'static tokio::runtime::Runtime, String> {
    static RUNTIME: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
    if let Some(runtime) = RUNTIME.get() {
        return Ok(runtime);
    }
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(RUNTIME_WORKER_THREADS)
        .enable_all()
        .build()
        .map_err(|error| format!("tokio runtime failed: {error}"))?;
    let _ = RUNTIME.set(runtime);
    Ok(RUNTIME.get().expect("quote runtime"))
}

/// One token to price in a batch run.
#[derive(Clone, Debug)]
pub struct ValuationTarget {
    pub token_address: String,
    pub token_symbol: Option<String>,
    pub token_decimals: u8,
}

/// A batch valuation report.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ValuationBatchReport {
    /// Source identifier for every observation in the report.
    pub source: String,
    /// RFC3339 timestamp of the report.
    pub observed_at: String,
    /// Nominal input amount used for every probe, in whole token units.
    pub nominal_amount: String,
    /// Protocols allowed for routing.
    pub protocols: Vec<String>,
    /// Number of targets requested.
    pub requested_count: usize,
    /// Number of probes actually performed.
    pub evaluated_count: usize,
    /// Number of tokens with a live route.
    pub route_found_count: usize,
    /// Number of tokens the service explicitly reported as unroutable.
    pub no_route_count: usize,
    /// Number of probes that could not be completed.
    pub unknown_count: usize,
    /// Per-token results.
    pub tokens: Vec<TokenValuation>,
}

impl ValuationBatchReport {
    /// Summary counters keyed by verdict, for compact reporting.
    pub fn verdict_counts(&self) -> BTreeMap<String, usize> {
        let mut counts = BTreeMap::new();
        for valuation in &self.tokens {
            *counts
                .entry(valuation.verdict.as_str().to_owned())
                .or_insert(0) += 1;
        }
        counts
    }

    /// Rebuilds a report from a set of individual observations.
    ///
    /// The durable valuation store keeps one row per token.  Reading them
    /// back has to produce the same shape a probe run would have produced,
    /// so that the economic gate cannot tell whether it was handed a fresh
    /// report or the persisted evidence — and therefore cannot make a
    /// different decision depending on where the evidence came from.
    ///
    /// The counters are recomputed from the observations rather than
    /// carried over, because they are a statement about the tokens that
    /// are actually present, not a claim about a run that may have covered
    /// a different population.
    pub fn from_tokens(tokens: Vec<TokenValuation>) -> Self {
        let mut route_found_count = 0usize;
        let mut no_route_count = 0usize;
        let mut unknown_count = 0usize;
        for token in &tokens {
            match token.verdict {
                ValuationVerdict::RouteFound => route_found_count += 1,
                ValuationVerdict::NoRoute => no_route_count += 1,
                _ => unknown_count += 1,
            }
        }
        let observed_at = tokens
            .iter()
            .map(|token| token.observed_at.as_str())
            .max()
            .unwrap_or_default()
            .to_owned();
        let protocols = tokens
            .iter()
            .flat_map(|token| token.protocols.iter().cloned())
            .collect::<BTreeSet<String>>()
            .into_iter()
            .collect();
        Self {
            source: SOURCE_ID.to_owned(),
            observed_at,
            nominal_amount: "1".to_owned(),
            protocols,
            requested_count: tokens.len(),
            evaluated_count: tokens.len(),
            route_found_count,
            no_route_count,
            unknown_count,
            tokens,
        }
    }
}

/// Prices many tokens against USDG.
///
/// The run uses a bounded pool of concurrent workers. The Trading API is a
/// shared hosted service, so the pool is deliberately small: enough to stop
/// a batch of two hundred tokens from taking hours, few enough to stay a
/// polite client.
pub fn value_tokens_to_usdg_batch(
    targets: &[ValuationTarget],
    nominal_amount: &str,
    config: &ValuationConfig,
) -> ValuationBatchReport {
    value_tokens_to_usdg_batch_with_concurrency(
        targets,
        nominal_amount,
        config,
        DEFAULT_CONCURRENCY,
    )
}

/// Runs the batch with an explicit worker count.
///
/// A worker count of zero or one means "no concurrency", which is what the
/// tests use: they assert on the exact order and content of the result, and
/// a concurrent run must not change either.
pub fn value_tokens_to_usdg_batch_with_concurrency(
    targets: &[ValuationTarget],
    nominal_amount: &str,
    config: &ValuationConfig,
    concurrency: usize,
) -> ValuationBatchReport {
    if concurrency <= 1 || targets.len() <= 1 {
        let tokens = targets
            .iter()
            .map(|target| {
                quote_token_to_usdg(
                    &target.token_address,
                    target.token_symbol.as_deref(),
                    target.token_decimals,
                    nominal_amount,
                    config,
                )
            })
            .collect::<Vec<_>>();
        return summarize_batch(tokens, nominal_amount, &config.protocols, targets.len());
    }

    // The workers share one runtime and one configuration. The results are
    // collected by index, so the report keeps the caller's order no matter
    // which worker finished first.
    let runtime = match quote_runtime() {
        Ok(runtime) => runtime,
        Err(_) => {
            // Without a runtime there is no way to make a request at all.
            // Every token is an explicit unknown, never a negative.
            let tokens = targets
                .iter()
                .map(|target| {
                    let mut valuation = TokenValuation::base(
                        &target.token_address,
                        target.token_symbol.as_deref(),
                        "0",
                        &config.protocols,
                    );
                    valuation.verdict = ValuationVerdict::ApiUnavailable;
                    valuation.failure_kind = Some(ValuationFailureKind::Transport);
                    valuation.failure_detail =
                        Some("the valuation runtime could not be started".to_owned());
                    valuation
                })
                .collect::<Vec<_>>();
            return summarize_batch(tokens, nominal_amount, &config.protocols, targets.len());
        }
    };

    let config = Arc::new(config.clone());
    let nominal_amount = nominal_amount.to_owned();
    let worker_count = concurrency.min(targets.len());
    let next_index = Arc::new(AtomicUsize::new(0));
    let targets = Arc::new(targets.to_vec());
    let slots: Arc<Vec<std::sync::Mutex<Option<TokenValuation>>>> = Arc::new(
        (0..targets.len())
            .map(|_| std::sync::Mutex::new(None))
            .collect(),
    );
    // The circuit breaker counts consecutive transport failures. When the
    // endpoint is unreachable there is no point in asking two hundred
    // times: the answer is the same every time, and every attempt costs the
    // full timeout. The breaker stops the batch and marks the remaining
    // tokens as unknown, never as negative.
    let consecutive_failures = Arc::new(AtomicUsize::new(0));
    let breaker_open = Arc::new(AtomicBool::new(false));

    runtime.block_on(async {
        let mut handles = Vec::with_capacity(worker_count);
        for _ in 0..worker_count {
            let config = Arc::clone(&config);
            let nominal_amount = nominal_amount.clone();
            let next_index = Arc::clone(&next_index);
            let consecutive_failures = Arc::clone(&consecutive_failures);
            let breaker_open = Arc::clone(&breaker_open);
            let slots = Arc::clone(&slots);
            let targets = Arc::clone(&targets);
            handles.push(tokio::task::spawn_blocking(move || {
                loop {
                    let index = next_index.fetch_add(1, Ordering::SeqCst);
                    if index >= targets.len() {
                        break;
                    }
                    let target = &targets[index];
                    if breaker_open.load(Ordering::SeqCst) {
                        // The breaker is open. The token is recorded as an
                        // explicit unknown, so the caller can tell "we stopped
                        // asking" apart from "we asked and there was no route".
                        let mut valuation = TokenValuation::base(
                            &target.token_address,
                            target.token_symbol.as_deref(),
                            "0",
                            &config.protocols,
                        );
                        valuation.verdict = ValuationVerdict::ApiUnavailable;
                        valuation.failure_kind = Some(ValuationFailureKind::Transport);
                        valuation.failure_detail = Some(
                            "the valuation endpoint was unreachable; the probe stopped asking"
                                .to_owned(),
                        );
                        *slots[index].lock().expect("valuation slot") = Some(valuation);
                        continue;
                    }
                    let valuation = quote_token_to_usdg(
                        &target.token_address,
                        target.token_symbol.as_deref(),
                        target.token_decimals,
                        &nominal_amount,
                        &config,
                    );
                    if valuation.failure_kind == Some(ValuationFailureKind::Transport) {
                        let failures = consecutive_failures.fetch_add(1, Ordering::SeqCst) + 1;
                        if failures >= CIRCUIT_BREAKER_THRESHOLD {
                            breaker_open.store(true, Ordering::SeqCst);
                        }
                    } else {
                        consecutive_failures.store(0, Ordering::SeqCst);
                    }
                    *slots[index].lock().expect("valuation slot") = Some(valuation);
                }
            }));
        }
        for handle in handles {
            let _ = handle.await;
        }
    });

    // The slots are shared by every worker, but the batch owns them
    // exclusively once the workers have finished, so the unwrap cannot
    // fail here.
    let slots = Arc::try_unwrap(slots).unwrap_or_else(|_| unreachable!("workers have finished"));
    let tokens = slots
        .into_iter()
        .map(|slot| {
            slot.into_inner()
                .expect("valuation slot")
                .expect("every slot is filled")
        })
        .collect::<Vec<_>>();
    summarize_batch(tokens, &nominal_amount, &config.protocols, targets.len())
}

/// Summarizes per-token valuations into a batch report.
pub fn summarize_batch(
    tokens: Vec<TokenValuation>,
    nominal_amount: &str,
    protocols: &[String],
    requested_count: usize,
) -> ValuationBatchReport {
    let route_found_count = tokens
        .iter()
        .filter(|valuation| valuation.verdict.proves_route())
        .count();
    let no_route_count = tokens
        .iter()
        .filter(|valuation| valuation.verdict.is_negative())
        .count();
    let unknown_count = tokens
        .iter()
        .filter(|valuation| valuation.verdict.is_unknown())
        .count();
    ValuationBatchReport {
        source: SOURCE_ID.to_owned(),
        observed_at: now_rfc3339(),
        nominal_amount: nominal_amount.to_owned(),
        protocols: protocols.to_vec(),
        requested_count,
        evaluated_count: tokens.len(),
        route_found_count,
        no_route_count,
        unknown_count,
        tokens,
    }
}

/// Current RFC3339 timestamp.
fn now_rfc3339() -> String {
    chrono::Utc::now().to_rfc3339()
}
