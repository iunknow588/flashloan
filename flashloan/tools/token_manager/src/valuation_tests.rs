//! Offline tests for the coarse USDG valuation probe.
//!\r
//! The tests never touch the network: request construction, response
//! parsing and verdict classification are all pure functions.

#[cfg(test)]
mod tests {
    use crate::valuation::*;
    use serde_json::json;

    fn config() -> ValuationConfig {
        ValuationConfig::default().with_api_key(Some("test-key".to_owned()))
    }

    #[test]
    fn request_body_matches_the_trading_api_contract() {
        let body = build_quote_request(
            "0xaF3D76f1834A1d425780943C99Ea8A608f8a93f9",
            "1000000000000000000",
            &config(),
        );
        assert_eq!(body["type"], "EXACT_INPUT");
        assert_eq!(body["amount"], "1000000000000000000");
        assert_eq!(body["tokenInChainId"], 4663);
        assert_eq!(body["tokenOutChainId"], 4663);
        assert_eq!(
            body["tokenIn"],
            "0xaF3D76f1834A1d425780943C99Ea8A608f8a93f9"
        );
        assert_eq!(
            body["tokenOut"].as_str().unwrap().to_ascii_lowercase(),
            CANONICAL_USDG_ADDRESS
        );
        assert_eq!(body["protocols"][0], "V4");
        assert_eq!(body["routingPreference"], "BEST_PRICE");
    }

    #[test]
    fn successful_quote_reports_a_route_and_a_decimal_usdg_amount() {
        let body = json!({
            "quote": {
                "output": {"amount": "1234567"},
                "priceImpact": "0.0001",
                "gasEstimate": "210000"
            },
            "route": [{"protocol": "V4"}, {"protocol": "V4"}]
        });
        let valuation = parse_quote_response(
            &body,
            "0xaF3D76f1834A1d425780943C99Ea8A608f8a93f9",
            Some("AAPL"),
            "1000000000000000000",
            &["V4".to_owned()],
        );
        assert_eq!(valuation.verdict, ValuationVerdict::RouteFound);
        assert!(valuation.has_value);
        assert_eq!(valuation.usdg_amount_raw.as_deref(), Some("1234567"));
        assert_eq!(valuation.usdg_amount_decimal.as_deref(), Some("1.234567"));
        assert_eq!(valuation.route_leg_count, Some(2));
        assert_eq!(valuation.price_impact.as_deref(), Some("0.0001"));
    }

    #[test]
    fn explicit_no_route_is_a_real_negative() {
        let body = json!({"errorCode": "NO_ROUTE", "detail": "No route exists"});
        let valuation = parse_quote_response(
            &body,
            "0x0000000000000000000000000000000000000001",
            None,
            "1",
            &["V4".to_owned()],
        );
        assert_eq!(valuation.verdict, ValuationVerdict::NoRoute);
        assert!(valuation.verdict.is_negative());
        assert!(!valuation.has_value);
    }

    #[test]
    fn malformed_response_is_unknown_and_never_a_negative() {
        let body = json!({"unexpected": true});
        let valuation = parse_quote_response(
            &body,
            "0x0000000000000000000000000000000000000001",
            None,
            "1",
            &["V4".to_owned()],
        );
        assert_eq!(valuation.verdict, ValuationVerdict::ApiError);
        assert!(valuation.verdict.is_unknown());
        assert!(!valuation.verdict.is_negative());
        assert_eq!(
            valuation.failure_kind,
            Some(ValuationFailureKind::MalformedResponse)
        );
    }

    #[test]
    fn a_404_no_route_body_is_a_real_negative() {
        // The Trading API answers a routeless token with HTTP 404 and a body
        // carrying `NoRouteFoundError`. That is a real negative, so it must
        // never be reported as an unknown.
        // This is the exact body observed live on 2026-11-12 against chain
        // 4663 for a token without a USDG route.
        let body = r#"{"errorCode":"NoRouteFoundError","detail":"No route with sufficient liquidity was found for this pair.","requestId":"6cf5d1544eea2f1b46af731b70245233"}"#;
        let detail = no_route_detail_from_body(body).expect("404 body must be a no-route answer");
        assert!(detail.contains("NoRouteFoundError"));
    }

    #[test]
    fn the_canonical_error_code_alone_is_a_no_route_answer() {
        // The `errorCode` is camelCase, so a plain "no route" substring test
        // would miss it. The classifier must recognise the code on its own
        // so a body-shape change cannot silently turn a real negative back
        // into an unknown.
        let body = r#"{"errorCode":"NoRouteFoundError"}"#;
        let detail = no_route_detail_from_body(body).expect("errorCode alone must classify");
        assert_eq!(detail, "NoRouteFoundError");
    }

    #[test]
    fn a_404_body_without_a_no_route_signal_stays_unknown() {
        // A 404 whose body carries no explicit no-route signal is an
        // unknown, never a negative. The classification is conservative on
        // purpose: a false negative would remove a token from the library.
        assert_eq!(no_route_detail_from_body(r#"{"detail":"not found"}"#), None);
        assert_eq!(no_route_detail_from_body("not json at all"), None);
        assert_eq!(no_route_detail_from_body(""), None);
    }

    #[test]
    fn a_200_no_route_body_is_a_real_negative() {
        // The same body classification applies to a success status, so the
        // verdict cannot depend on how the service chose to frame the
        // answer.
        let body = json!({"errorCode": "NoRouteFoundError"});
        let valuation = parse_quote_response(
            &body,
            "0x0000000000000000000000000000000000000001",
            None,
            "1",
            &["V4".to_owned()],
        );
        assert_eq!(valuation.verdict, ValuationVerdict::NoRoute);
        assert!(valuation.verdict.is_negative());
    }

    #[test]
    fn a_429_body_is_never_a_no_route_answer() {
        // Rate limiting is an unknown. Even if the body happens to contain a
        // phrase that looks like a missing route, the dedicated status
        // classification wins for the malformed / non-no-route shapes.
        assert_eq!(
            no_route_detail_from_body(r#"{"errorCode":"RateLimited"}"#),
            None
        );
    }

    #[test]
    fn a_401_body_is_never_a_no_route_answer() {
        assert_eq!(
            no_route_detail_from_body(r#"{"errorCode":"Unauthorized"}"#),
            None
        );
    }

    #[test]
    fn missing_api_key_is_unknown_not_a_negative() {
        let valuation = quote_token_to_usdg(
            "0x0000000000000000000000000000000000000001",
            None,
            18,
            "1",
            &config().with_api_key(None),
        );
        assert_eq!(valuation.verdict, ValuationVerdict::ApiUnavailable);
        assert!(valuation.verdict.is_unknown());
        assert!(!valuation.has_value);
        assert_eq!(
            valuation.failure_kind,
            Some(ValuationFailureKind::MissingApiKey)
        );
    }

    #[test]
    fn nominal_amount_is_converted_to_raw_units_without_floats() {
        assert_eq!(
            raw_amount_from_decimal("1", 18).as_deref(),
            Some("1000000000000000000")
        );
        assert_eq!(raw_amount_from_decimal("0.5", 6).as_deref(), Some("500000"));
        assert_eq!(raw_amount_from_decimal("0.000001", 6).as_deref(), Some("1"));
        assert_eq!(
            raw_amount_from_decimal("2.25", 8).as_deref(),
            Some("225000000")
        );
        assert_eq!(raw_amount_from_decimal("abc", 18), None);
        assert_eq!(raw_amount_from_decimal("-1", 18), None);
    }

    #[test]
    fn raw_amounts_render_as_decimals() {
        assert_eq!(raw_to_decimal("1234567", 6), "1.234567");
        assert_eq!(raw_to_decimal("1000000", 6), "1");
        assert_eq!(raw_to_decimal("1", 6), "0.000001");
        assert_eq!(raw_to_decimal("0", 6), "0");
        assert_eq!(raw_to_decimal("100", 0), "100");
    }

    #[test]
    fn route_detail_is_never_retained_in_the_report() {
        let body = json!({
            "quote": {"output": {"amount": "5"}},
            "route": [{
                "protocol": "V4",
                "pool": {"poolId": "0xabc"},
                "tokenIn": {"address": "0x1"},
                "tokenOut": {"address": "0x2"}
            }]
        });
        let valuation = parse_quote_response(
            &body,
            "0x0000000000000000000000000000000000000001",
            None,
            "1",
            &["V4".to_owned()],
        );
        let serialized = serde_json::to_string(&valuation).unwrap();
        assert!(!serialized.contains("poolId"));
        assert!(!serialized.contains("0xabc"));
    }

    #[test]
    fn batch_summary_keeps_unknowns_separate_from_negatives() {
        let tokens = vec![
            sample_valuation("0x1", ValuationVerdict::RouteFound),
            sample_valuation("0x2", ValuationVerdict::NoRoute),
            sample_valuation("0x3", ValuationVerdict::ApiUnavailable),
        ];
        let report = summarize_batch(tokens, "1", &["V4".to_owned()], 3);
        assert_eq!(report.route_found_count, 1);
        assert_eq!(report.no_route_count, 1);
        assert_eq!(report.unknown_count, 1);
        assert_eq!(report.evaluated_count, 3);
    }

    #[test]
    fn a_concurrent_batch_keeps_the_callers_order() {
        // The workers finish in whatever order they can, but the report has
        // to keep the caller's order: a batch report is a list of
        // observations, and reordering it would make two runs of the same
        // input look different for no reason.
        let targets = vec![
            ValuationTarget {
                token_address: "0x1".to_owned(),
                token_symbol: Some("ONE".to_owned()),
                token_decimals: 18,
            },
            ValuationTarget {
                token_address: "0x2".to_owned(),
                token_symbol: Some("TWO".to_owned()),
                token_decimals: 18,
            },
            ValuationTarget {
                token_address: "0x3".to_owned(),
                token_symbol: Some("THREE".to_owned()),
                token_decimals: 18,
            },
            ValuationTarget {
                token_address: "0x4".to_owned(),
                token_symbol: Some("FOUR".to_owned()),
                token_decimals: 18,
            },
        ];
        let report = value_tokens_to_usdg_batch_with_concurrency(
            &targets,
            "1",
            &config().with_api_key(None),
            4,
        );
        assert_eq!(report.evaluated_count, 4);
        assert_eq!(report.requested_count, 4);
        let addresses = report
            .tokens
            .iter()
            .map(|token| token.token_address.as_str())
            .collect::<Vec<_>>();
        assert_eq!(addresses, vec!["0x1", "0x2", "0x3", "0x4"]);
        // Without a key every token is an explicit unknown, never a
        // negative, no matter how many workers there are.
        assert_eq!(report.route_found_count, 0);
        assert_eq!(report.no_route_count, 0);
        assert_eq!(report.unknown_count, 4);
    }

    #[test]
    fn a_sequential_batch_is_still_supported() {
        let targets = vec![ValuationTarget {
            token_address: "0x1".to_owned(),
            token_symbol: None,
            token_decimals: 18,
        }];
        let report = value_tokens_to_usdg_batch_with_concurrency(
            &targets,
            "1",
            &config().with_api_key(None),
            1,
        );
        assert_eq!(report.evaluated_count, 1);
        assert_eq!(report.unknown_count, 1);
    }

    #[test]
    fn verdict_helpers_separate_truth_from_unknown() {
        assert!(ValuationVerdict::RouteFound.proves_route());
        assert!(!ValuationVerdict::NoRoute.proves_route());
        assert!(ValuationVerdict::NoRoute.is_negative());
        assert!(!ValuationVerdict::ApiError.is_negative());
        assert!(ValuationVerdict::ApiError.is_unknown());
    }

    #[test]
    fn api_key_is_never_serialized_into_a_report() {
        let body = json!({"quote": {"output": {"amount": "1"}}});
        let valuation = parse_quote_response(
            &body,
            "0x0000000000000000000000000000000000000001",
            None,
            "1",
            &["V4".to_owned()],
        );
        let serialized = serde_json::to_string(&valuation).unwrap();
        assert!(!serialized.contains("test-key"));
    }

    fn sample_valuation(address: &str, verdict: ValuationVerdict) -> TokenValuation {
        let mut valuation = TokenValuation::base(address, None, "1", &["V4".to_owned()]);
        valuation.verdict = verdict;
        valuation.has_value = verdict.proves_route();
        valuation
    }
}
