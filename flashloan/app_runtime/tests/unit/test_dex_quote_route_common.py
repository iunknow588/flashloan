from market.binance_market.dex_quote_route_common import (
    build_quote_analysis_defaults,
    build_quote_inputs_bundle,
    build_quote_request_specs,
    build_quote_verification_payload,
    quote_route_status,
)
from market.binance_market.dex_surface_analysis import build_route_size_analysis
from market.binance_market.dex_quote_route_policy import QuoteLiveTestPolicy
from market.binance_market.dex_quote_surface import build_quote_request_payload, build_quote_routes_payload_from_overview


def test_quote_route_common_templates_cover_amm_v4_uniswapx():
    analysis = build_quote_analysis_defaults(
        quote_analysis={"executableCandidate": True},
        route_symbols=["ETH", "TK1", "ETH"],
        price_evidence_source="custom_source",
        price_evidence_mode="custom_mode",
    )

    pools = [
        {"pool": "0x1", "protocol": "uniswap_v4", "fee": 0},
        {"pool": "0x2", "protocol": "uniswap_v3", "fee": 3000},
    ]
    requests = build_quote_request_specs(
        pair_symbol="ETH -> TK1 -> ETH",
        pools=pools,
        route_symbols=["ETH", "TK1", "ETH"],
        route_addresses=["0x0", "0x1", "0x0"],
        route_tokens=[{"symbol": "ETH"}, {"symbol": "TK1"}, {"symbol": "ETH"}],
        amm_quote_mode="overview_route_snapshot",
        amm_status="waiting_live_price",
        v4_quote_mode="overview_route_snapshot",
        v4_status="waiting_live_price",
        uniswapx_quote_mode="intent_exact_input",
        uniswapx_status="waiting_live_price",
        chain_id=46630,
        settlement={"uniswapx": {"supported": True, "reactor_family": "exclusive"}},
        adapter_hints=["pool_manager_state_required"],
    )
    bundle = build_quote_inputs_bundle(
        candidate_pools=pools[:1],
        route_pools=pools,
        quote_requests=requests,
        conditions=["framework only", "live verify"],
    )

    assert analysis["priceEvidenceSource"] == "custom_source"
    assert analysis["priceEvidenceMode"] == "custom_mode"
    assert quote_route_status(analysis) == "static_probe_ready_for_precheck"
    assert {request["venue"] for request in requests} == {"amm_pool", "uniswap_v4", "uniswapx"}
    assert bundle["candidatePools"] == pools[:1]
    assert bundle["routePools"] == pools
    assert bundle["quoteRequests"] == requests
    assert bundle["conditions"] == ["framework only", "live verify"]


def test_quote_route_status_and_request_payload_surface_live_verification():
    overview = {
        "arbitragePaths": [
            {
                "symbolPair": "ETH -> TK1 -> ETH",
                "routeSymbols": ["ETH", "TK1", "ETH"],
                "routeAddresses": ["0x0", "0x1", "0x0"],
                "routeTokenPath": [{"symbol": "ETH"}, {"symbol": "TK1"}, {"symbol": "ETH"}],
                "routePools": [{"pool": "0x1", "protocol": "uniswap_v4", "fee": 0}],
                "quoteInputs": {
                    "candidatePools": [{"pool": "0x1", "protocol": "uniswap_v4", "fee": 0}],
                    "routePools": [{"pool": "0x1", "protocol": "uniswap_v4", "fee": 0}],
                    "quoteRequests": [{"venue": "uniswap_v4", "quoteMode": "uniswap_v4_sdk_exact_input_route", "status": "sdk_adapter_required"}],
                    "conditions": ["framework only", "live verify"],
                },
                "overviewRoute": {
                    "pathSymbols": ["ETH", "TK1", "ETH"],
                    "pathAddresses": ["0x0", "0x1", "0x0"],
                    "pathTokens": [{"symbol": "ETH"}, {"symbol": "TK1"}, {"symbol": "ETH"}],
                },
                "quoteAnalysis": {
                    "complete": True,
                    "requiresLiveQuoter": False,
                    "adapterRequired": False,
                    "livePriceVerified": True,
                    "priceEvidenceMode": "live_verified",
                    "priceEvidenceSource": "chain_live_quote",
                    "profitable": True,
                    "executableCandidate": True,
                    "chainSubmissionCandidate": True,
                    "chainSubmissionPolicy": "live_framework_reuse",
                    "recommendation": {"level": "ready_for_precheck", "action": "进入链上复核", "reasons": []},
                },
                "quoteInputs": {"candidatePools": [], "routePools": [], "quoteRequests": []},
            }
        ],
        "summary": {},
        "marketObservation": {"summary": {}, "chainSpecificObservationPairs": []},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {},
    }

    payload = build_quote_routes_payload_from_overview(
        overview,
        chain_id=46630,
        protocols=["uniswap_v4"],
        route_quote_analysis_fn=lambda route, price_map, chain_id: {
            "steps": [{"fromSymbol": "ETH", "toSymbol": "TK1"}],
            "complete": True,
            "requiresLiveQuoter": False,
            "adapterRequired": False,
            "livePriceVerified": True,
            "priceEvidenceSource": "chain_live_quote",
            "priceEvidenceMode": "live_verified",
            "executableCandidate": True,
            "chainSubmissionCandidate": True,
            "chainSubmissionPolicy": "live_framework_reuse",
            "netProfitUsd": 1.0,
        },
        route_size_analysis_fn=lambda route, price_map, chain_id: {"rows": [{"size": 1}], "bestObservedNetProfitUsd": 1.0},
    )
    route = payload["routes"][0]
    request = build_quote_request_payload(
        quote_routes_payload=payload,
        pair=route["symbolPair"],
        venue="uniswap_v4",
        amount="1000",
    )

    assert quote_route_status(route["quoteAnalysis"]) == "live_verified"
    assert build_quote_verification_payload(quote_analysis=route["quoteAnalysis"])["status"] == "live_verified"
    assert route["quoteStatus"] == "live_verified"
    assert route["quoteVerification"]["status"] == "live_verified"
    assert route["quoteVerification"]["livePriceVerified"] is True
    assert request["quoteStatus"] == "live_verified"
    assert request["quoteVerification"]["status"] == "live_verified"
    assert request["quoteVerification"]["trustedForExecution"] is True


def test_quote_routes_payload_from_overview_keeps_surface_and_analysis_aligned():
    overview = {
        "arbitragePaths": [
            {
                "symbolPair": "ETH -> TK1 -> ETH",
                "routeSymbols": ["ETH", "TK1", "ETH"],
                "routeAddresses": ["0x0", "0x1", "0x0"],
                "routeTokenPath": [{"symbol": "ETH"}, {"symbol": "TK1"}, {"symbol": "ETH"}],
                "routePools": [{"pool": "0x1", "protocol": "uniswap_v4", "fee": 0}],
                "quoteInputs": {
                    "candidatePools": [{"pool": "0x1", "protocol": "uniswap_v4", "fee": 0}],
                    "routePools": [{"pool": "0x1", "protocol": "uniswap_v4", "fee": 0}],
                    "quoteRequests": [{"venue": "amm_pool", "quoteMode": "overview_route_snapshot", "status": "waiting_live_price"}],
                    "conditions": ["framework only"],
                },
                "overviewRoute": {
                    "pathSymbols": ["ETH", "TK1", "ETH"],
                    "pathAddresses": ["0x0", "0x1", "0x0"],
                    "pathTokens": [{"symbol": "ETH"}, {"symbol": "TK1"}, {"symbol": "ETH"}],
                },
                "quoteAnalysis": {"executableCandidate": True},
            }
        ],
        "summary": {},
        "marketObservation": {"summary": {}, "chainSpecificObservationPairs": []},
        "dexSettlement": {"uniswapx": {"supported": True, "reactor_family": "exclusive"}},
        "poolStaticTable": {},
    }

    payload = build_quote_routes_payload_from_overview(
        overview,
        chain_id=46630,
        protocols=["uniswap_v4"],
        route_quote_analysis_fn=lambda route, price_map, chain_id: {
            "steps": [{"fromSymbol": "ETH", "toSymbol": "TK1"}],
            "complete": True,
            "executableCandidate": True,
            "priceEvidenceSource": "live_quoter",
            "priceEvidenceMode": "live_required",
            "netProfitUsd": 1.0,
        },
        route_size_analysis_fn=lambda route, price_map, chain_id: {"rows": [{"size": 1}], "bestObservedNetProfitUsd": 1.0},
    )

    route = payload["routes"][0]

    assert payload["summary"]["routeCount"] == 1
    assert route["quoteAnalysis"]["priceEvidenceSource"] == "live_quoter"
    assert route["quoteAnalysis"]["priceEvidenceMode"] == "live_required"
    assert route["sizeAnalysis"]["rows"] == [{"size": 1}]
    assert route["quoteInputs"]["quoteRequests"]


def test_static_quote_route_fills_multi_hop_steps_on_mainnet(monkeypatch):
    monkeypatch.setenv("DEX_QUOTE_ROUTE_LIVE_ROUTE_LIMIT", "0")
    monkeypatch.setenv("DEX_QUOTE_ROUTE_SIZE_PROBE_LIMIT", "0")
    overview = {
        "arbitragePaths": [
            {
                "symbolPair": "ETH -> TK1 -> TK2 -> ETH",
                "routeSymbols": ["ETH", "TK1", "TK2", "ETH"],
                "routeAddresses": ["0x0", "0x1", "0x2", "0x0"],
                "routeTokenPath": [{"symbol": "ETH"}, {"symbol": "TK1"}, {"symbol": "TK2"}, {"symbol": "ETH"}],
                "routePools": [
                    {"pool": "0x10", "protocol": "uniswap_v4", "fee": 0, "routeSymbols": ["ETH", "TK1"]},
                    {"pool": "0x11", "protocol": "uniswap_v4", "fee": 0, "routeSymbols": ["TK1", "TK2"]},
                    {"pool": "0x12", "protocol": "uniswap_v4", "fee": 0, "routeSymbols": ["TK2", "ETH"]},
                ],
                "overviewRoute": {
                    "pathSymbols": ["ETH", "TK1", "TK2", "ETH"],
                    "pathAddresses": ["0x0", "0x1", "0x2", "0x0"],
                    "pathTokens": [{"symbol": "ETH"}, {"symbol": "TK1"}, {"symbol": "TK2"}, {"symbol": "ETH"}],
                },
            }
        ],
        "summary": {},
        "marketObservation": {"summary": {}, "chainSpecificObservationPairs": []},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {},
    }

    payload = build_quote_routes_payload_from_overview(
        overview,
        chain_id=4663,
        protocols=["uniswap_v4"],
        route_quote_analysis_fn=lambda *_args, **_kwargs: {},
        route_size_analysis_fn=lambda *_args, **_kwargs: {},
    )

    steps = payload["routes"][0]["quoteAnalysis"]["steps"]
    assert len(steps) == 3
    assert [step["fromSymbol"] for step in steps] == ["ETH", "TK1", "TK2"]
    assert [step["toSymbol"] for step in steps] == ["TK1", "TK2", "ETH"]
    assert [step["hop"] for step in steps] == [1, 2, 3]
    assert all(step["protocol"] == "uniswap_v4" for step in steps)


def test_offchain_size_ladder_emits_recommended_raw_borrow_amount():
    route = {
        "routeSymbols": ["ETH", "TK1", "ETH"],
        "routeTokenPath": [
            {"symbol": "ETH", "decimals": 18},
            {"symbol": "TK1", "decimals": 18},
            {"symbol": "ETH", "decimals": 18},
        ],
    }

    def quote_analysis(_route, *, price_map, chain_id, notional_usd=1000.0, token_basis_input_amount=None):
        amount = token_basis_input_amount or 1.0
        return {
            "complete": True,
            "requiresLiveQuoter": False,
            "adapterRequired": False,
            "referenceInputAmount": amount,
            "referenceInputSymbol": "ETH",
            "referenceFinalOutputAmount": amount * 1.01,
            "referenceFinalOutputSymbol": "ETH",
            "referenceNetProfitAmount": amount * 0.0095,
            "referenceNetReturnBps": 95.0,
            "referenceReturnBasis": "borrow_token",
            "referenceReturnTrusted": True,
            "tokenBasisReferenceProfitable": True,
            "tokenBasisProfitable": True,
            "chainSubmissionCandidate": True,
            "executableCandidate": True,
            "observedNetProfitUsd": amount,
            "netProfitUsd": amount,
        }

    analysis = build_route_size_analysis(
        route,
        price_map={},
        chain_id=46630,
        route_quote_analysis_fn=quote_analysis,
    )

    assert analysis["recommendedBorrowInputAmount"] == 64.0
    assert analysis["recommendedBorrowInputAmountRaw"] == str(64 * 10**18)
    assert analysis["recommendedBorrowInputAmountBasis"] == "borrow_token"
    assert analysis["recommendedBorrowInputAmountReason"] == "best_positive_observed_net_profit"


def test_token_basis_amount_steps_use_doubling_halving_from_base_one(monkeypatch):
    from market.binance_market.dex_surface_analysis_impl import _token_basis_amount_steps

    monkeypatch.delenv("DEX_QUOTE_TOKEN_AMOUNT_STEPS", raising=False)
    monkeypatch.delenv("DEX_QUOTE_TOKEN_BASE_AMOUNT", raising=False)
    monkeypatch.delenv("DEX_QUOTE_TOKEN_HALVING_STEPS", raising=False)
    monkeypatch.delenv("DEX_QUOTE_TOKEN_DOUBLING_STEPS", raising=False)

    steps = _token_basis_amount_steps()

    assert steps[0] == 0.0625
    assert 0.5 in steps
    assert 1.0 in steps
    assert 64.0 in steps
    assert all(round(steps[index + 1] / steps[index], 12) == 2.0 for index in range(len(steps) - 1))


def test_quote_routes_payload_limits_expensive_size_probes_for_late_non_testnet_routes(monkeypatch):
    overview = {
        "arbitragePaths": [
            {
                "symbolPair": "ETH -> TK1 -> ETH",
                "routeSymbols": ["ETH", "TK1", "ETH"],
                "routeAddresses": ["0x0", "0x1", "0x0"],
                "routeTokenPath": [{"symbol": "ETH"}, {"symbol": "TK1"}, {"symbol": "ETH"}],
                "routePools": [{"pool": "0x1", "protocol": "uniswap_v4", "fee": 0}],
                "overviewRoute": {"pathSymbols": ["ETH", "TK1", "ETH"], "pathAddresses": ["0x0", "0x1", "0x0"], "pathTokens": [{"symbol": "ETH"}, {"symbol": "TK1"}, {"symbol": "ETH"}]},
                "quoteAnalysis": {"complete": True, "executableCandidate": False, "chainSubmissionCandidate": False},
            },
            {
                "symbolPair": "ETH -> TK2 -> ETH",
                "routeSymbols": ["ETH", "TK2", "ETH"],
                "routeAddresses": ["0x0", "0x2", "0x0"],
                "routeTokenPath": [{"symbol": "ETH"}, {"symbol": "TK2"}, {"symbol": "ETH"}],
                "routePools": [{"pool": "0x2", "protocol": "uniswap_v4", "fee": 0}],
                "overviewRoute": {"pathSymbols": ["ETH", "TK2", "ETH"], "pathAddresses": ["0x0", "0x2", "0x0"], "pathTokens": [{"symbol": "ETH"}, {"symbol": "TK2"}, {"symbol": "ETH"}]},
                "quoteAnalysis": {"complete": True, "executableCandidate": False, "chainSubmissionCandidate": False},
            },
        ],
        "summary": {},
        "marketObservation": {"summary": {}, "chainSpecificObservationPairs": []},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {},
    }
    calls = {"size": 0}

    def route_quote_analysis_fn(route, price_map, chain_id):
        return {
            "steps": [{"fromSymbol": "ETH", "toSymbol": route["routeSymbols"][1]}],
            "complete": True,
            "requiresLiveQuoter": False,
            "adapterRequired": False,
            "livePriceVerified": False,
            "priceEvidenceSource": "static_reference_probe",
            "priceEvidenceMode": "reference_only",
            "executableCandidate": False,
            "chainSubmissionCandidate": False,
            "chainSubmissionPolicy": "live_framework_reuse",
            "netProfitUsd": None,
        }

    def route_size_analysis_fn(route, price_map, chain_id):
        calls["size"] += 1
        return {"rows": [{"size": 1}], "bestObservedNetProfitUsd": 1.0}

    monkeypatch.setenv("DEX_QUOTE_ROUTE_SIZE_PROBE_LIMIT", "1")
    payload = build_quote_routes_payload_from_overview(
        overview,
        chain_id=56,
        protocols=["uniswap_v4"],
        route_quote_analysis_fn=route_quote_analysis_fn,
        route_size_analysis_fn=route_size_analysis_fn,
    )

    assert calls["size"] == 1
    assert payload["routes"][0]["sizeAnalysis"]["rows"] == [{"size": 1}]
    assert payload["routes"][1]["sizeAnalysis"] == {}


def test_quote_routes_payload_probes_all_robinhood_testnet_routes(monkeypatch):
    overview = {
        "arbitragePaths": [
            {
                "symbolPair": "ETH -> TK1 -> ETH",
                "routeSymbols": ["ETH", "TK1", "ETH"],
                "routeAddresses": ["0x0", "0x1", "0x0"],
                "routeTokenPath": [{"symbol": "ETH"}, {"symbol": "TK1"}, {"symbol": "ETH"}],
                "routePools": [{"pool": "0x1", "protocol": "uniswap_v4", "fee": 0}],
                "overviewRoute": {"pathSymbols": ["ETH", "TK1", "ETH"], "pathAddresses": ["0x0", "0x1", "0x0"], "pathTokens": [{"symbol": "ETH"}, {"symbol": "TK1"}, {"symbol": "ETH"}]},
                "quoteAnalysis": {"complete": True, "executableCandidate": False, "chainSubmissionCandidate": False},
            },
            {
                "symbolPair": "ETH -> TK2 -> ETH",
                "routeSymbols": ["ETH", "TK2", "ETH"],
                "routeAddresses": ["0x0", "0x2", "0x0"],
                "routeTokenPath": [{"symbol": "ETH"}, {"symbol": "TK2"}, {"symbol": "ETH"}],
                "routePools": [{"pool": "0x2", "protocol": "uniswap_v4", "fee": 0}],
                "overviewRoute": {"pathSymbols": ["ETH", "TK2", "ETH"], "pathAddresses": ["0x0", "0x2", "0x0"], "pathTokens": [{"symbol": "ETH"}, {"symbol": "TK2"}, {"symbol": "ETH"}]},
                "quoteAnalysis": {"complete": True, "executableCandidate": False, "chainSubmissionCandidate": False},
            },
        ],
        "summary": {},
        "marketObservation": {"summary": {}, "chainSpecificObservationPairs": []},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {},
    }
    calls = {"size": 0}

    def route_quote_analysis_fn(route, price_map, chain_id):
        return {
            "steps": [{"fromSymbol": "ETH", "toSymbol": route["routeSymbols"][1]}],
            "complete": True,
            "requiresLiveQuoter": False,
            "adapterRequired": False,
            "livePriceVerified": False,
            "priceEvidenceSource": "static_reference_probe",
            "priceEvidenceMode": "reference_only",
            "executableCandidate": False,
            "chainSubmissionCandidate": False,
            "chainSubmissionPolicy": "live_framework_reuse",
            "netProfitUsd": None,
        }

    def route_size_analysis_fn(route, price_map, chain_id):
        calls["size"] += 1
        return {"rows": [{"size": calls["size"]}], "bestObservedNetProfitUsd": float(calls["size"])}

    monkeypatch.setenv("DEX_QUOTE_ROUTE_SIZE_PROBE_LIMIT", "0")
    monkeypatch.setenv("DEX_QUOTE_ROUTE_PROBE_ALL_TESTNET_ROUTES", "1")
    payload = build_quote_routes_payload_from_overview(
        overview,
        chain_id=46630,
        protocols=["uniswap_v4"],
        route_quote_analysis_fn=route_quote_analysis_fn,
        route_size_analysis_fn=route_size_analysis_fn,
    )

    assert calls["size"] == 2
    assert payload["routes"][0]["sizeAnalysis"]["rows"] == [{"size": 1}]
    assert payload["routes"][1]["sizeAnalysis"]["rows"] == [{"size": 2}]


def test_quote_routes_payload_skips_live_refresh_when_cached_framework_steps_exist(monkeypatch):
    overview = {
        "arbitragePaths": [
            {
                "symbolPair": "ETH -> TK1 -> ETH",
                "routeSymbols": ["ETH", "TK1", "ETH"],
                "routeAddresses": ["0x0", "0x1", "0x0"],
                "routeTokenPath": [{"symbol": "ETH"}, {"symbol": "TK1"}, {"symbol": "ETH"}],
                "routePools": [{"pool": "0x1", "protocol": "uniswap_v4", "fee": 0}],
                "overviewRoute": {"pathSymbols": ["ETH", "TK1", "ETH"], "pathAddresses": ["0x0", "0x1", "0x0"], "pathTokens": [{"symbol": "ETH"}, {"symbol": "TK1"}, {"symbol": "ETH"}]},
                "quoteAnalysis": {"steps": [{"fromSymbol": "ETH", "toSymbol": "TK1"}], "executableCandidate": False},
                "quoteInputs": {
                    "candidatePools": [{"pool": "0x1", "protocol": "uniswap_v4", "fee": 0}],
                    "routePools": [{"pool": "0x1", "protocol": "uniswap_v4", "fee": 0}],
                    "quoteRequests": [],
                    "conditions": ["framework only"],
                },
            }
        ],
        "summary": {},
        "marketObservation": {"summary": {}, "chainSpecificObservationPairs": []},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {},
    }
    calls = {"quote": 0, "size": 0}

    def route_quote_analysis_fn(route, price_map, chain_id):
        calls["quote"] += 1
        return {
            "steps": [{"fromSymbol": "ETH", "toSymbol": "TK1"}],
            "complete": True,
            "requiresLiveQuoter": False,
            "adapterRequired": False,
            "livePriceVerified": False,
            "priceEvidenceSource": "chain_live_quote",
            "priceEvidenceMode": "live_required",
            "executableCandidate": False,
            "chainSubmissionCandidate": False,
            "chainSubmissionPolicy": "live_framework_reuse",
            "netProfitUsd": None,
        }

    def route_size_analysis_fn(route, price_map, chain_id):
        calls["size"] += 1
        return {"rows": [{"size": 1}], "bestObservedNetProfitUsd": 1.0}

    monkeypatch.setenv("DEX_QUOTE_ROUTE_SIZE_PROBE_LIMIT", "0")
    monkeypatch.setenv("DEX_QUOTE_ROUTE_PROBE_ALL_TESTNET_ROUTES", "1")
    payload = build_quote_routes_payload_from_overview(
        overview,
        chain_id=46630,
        protocols=["uniswap_v4"],
        route_quote_analysis_fn=route_quote_analysis_fn,
        route_size_analysis_fn=route_size_analysis_fn,
    )

    assert calls["quote"] == 0
    assert calls["size"] == 1
    assert payload["routes"][0]["quoteAnalysis"]["steps"] == [{"fromSymbol": "ETH", "toSymbol": "TK1"}]
    assert payload["routes"][0]["quoteAnalysis"]["executableCandidate"] is False

    monkeypatch.setenv("DEX_QUOTE_ROUTE_FORCE_REFRESH", "1")
    refreshed = build_quote_routes_payload_from_overview(
        overview,
        chain_id=46630,
        protocols=["uniswap_v4"],
        route_quote_analysis_fn=route_quote_analysis_fn,
        route_size_analysis_fn=route_size_analysis_fn,
    )

    assert calls["quote"] == 1
    assert calls["size"] == 2
    assert refreshed["routes"][0]["quoteAnalysis"]["priceEvidenceSource"] == "chain_live_quote"


def test_quote_routes_payload_reviews_robinhood_testnet_by_default(monkeypatch):
    overview = {
        "arbitragePaths": [
            {
                "symbolPair": "TZ -> TB -> TA -> TZ",
                "routeSymbols": ["TZ", "TB", "TA", "TZ"],
                "routeAddresses": ["0x0", "0x1", "0x2", "0x0"],
                "routeTokenPath": [{"symbol": "TZ"}, {"symbol": "TB"}, {"symbol": "TA"}, {"symbol": "TZ"}],
                "routePools": [{"pool": "0x1", "protocol": "uniswap_v4", "fee": 0}],
                "overviewRoute": {
                    "pathSymbols": ["TZ", "TB", "TA", "TZ"],
                    "pathAddresses": ["0x0", "0x1", "0x2", "0x0"],
                    "pathTokens": [{"symbol": "TZ"}, {"symbol": "TB"}, {"symbol": "TA"}, {"symbol": "TZ"}],
                },
            }
        ],
        "summary": {},
        "marketObservation": {"summary": {}, "chainSpecificObservationPairs": []},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {},
    }
    calls = {"quote": 0, "size": 0}

    def route_quote_analysis_fn(route, price_map, chain_id):
        calls["quote"] += 1
        return {
            "steps": [{"fromSymbol": "TZ", "toSymbol": "TB"}],
            "complete": True,
            "requiresLiveQuoter": False,
            "adapterRequired": False,
            "livePriceVerified": True,
            "priceEvidenceSource": "chain_live_quote",
            "priceEvidenceMode": "live_verified",
            "executableCandidate": False,
            "chainSubmissionCandidate": False,
            "chainSubmissionPolicy": "live_framework_reuse",
            "netProfitUsd": None,
        }

    def route_size_analysis_fn(route, price_map, chain_id):
        calls["size"] += 1
        return {"rows": [{"size": 1}]}

    monkeypatch.setenv("DEX_QUOTE_ROUTE_SIZE_PROBE_LIMIT", "0")
    monkeypatch.setenv("DEX_QUOTE_ROUTE_LIVE_ROUTE_LIMIT", "999")
    payload = build_quote_routes_payload_from_overview(
        overview,
        chain_id=46630,
        protocols=["uniswap_v4"],
        route_quote_analysis_fn=route_quote_analysis_fn,
        route_size_analysis_fn=route_size_analysis_fn,
    )

    assert calls["quote"] == 1
    assert calls["size"] == 1
    assert payload["routes"][0]["quoteAnalysis"]["priceEvidenceMode"] == "live_verified"
    assert payload["routes"][0]["quoteStatus"] == "live_verified"

def test_route_steps_order_and_continuity():
    """Steps must be ordered by hop and match route symbols end-to-end."""
    from market.binance_market.dex_surface_route_quotes import _route_quote_analysis

    route = {
        "routeSymbols": ["TZ", "TB", "TA", "TZ"],
        "routeAddresses": ["0xA", "0xB", "0xC", "0xA"],
        "quoteInputs": {"routePools": []},
    }
    # Mock price map so analysis completes
    price_map = {"TZ": 1.0, "TB": 1.0, "TA": 1.0}
    analysis = _route_quote_analysis(route, price_map=price_map, chain_id=46630, token_basis_input_amount=0.1)

    steps = analysis.get("steps") or []
    # Steps must be sorted by hop
    hops = [int(s.get("hop") or 0) for s in steps]
    assert hops == sorted(hops), "steps not ordered by hop"
    # Continuity with route symbols
    if steps:
        assert steps[0].get("fromSymbol") == "TZ", "first step fromSymbol mismatch route"
        assert steps[-1].get("toSymbol") == "TZ", "last step toSymbol mismatch route"
        # No route steps mismatch warning
        warnings = analysis.get("warnings") or []
        assert "route_steps_mismatch" not in warnings, "route steps mismatch detected"


def _anchor_overview_with_two_routes():
    return {
        "arbitragePaths": [
            {
                "symbolPair": "ETH -> AAA -> BBB -> ETH",
                "routeSymbols": ["ETH", "AAA", "BBB", "ETH"],
                "routeAddresses": ["0xA", "0xB", "0xC", "0xA"],
                "routeTokenPath": [{"symbol": "ETH"}, {"symbol": "AAA"}, {"symbol": "BBB"}, {"symbol": "ETH"}],
                "routePools": [{"pool": "0x1", "protocol": "uniswap_v4", "fee": 0}],
                "overviewRoute": {
                    "kind": "bridge_pair",
                    "anchorBorrowSymbol": "ETH",
                    "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
                },
            },
            {
                "symbolPair": "USDC -> AAA -> BBB -> USDC",
                "routeSymbols": ["USDC", "AAA", "BBB", "USDC"],
                "routeAddresses": ["0xD", "0xB", "0xC", "0xD"],
                "routeTokenPath": [{"symbol": "USDC"}, {"symbol": "AAA"}, {"symbol": "BBB"}, {"symbol": "USDC"}],
                "routePools": [{"pool": "0x2", "protocol": "uniswap_v4", "fee": 0}],
                "overviewRoute": {
                    "kind": "bridge_pair",
                    "anchorBorrowSymbol": "USDC",
                    "pathSymbols": ["USDC", "AAA", "BBB", "USDC"],
                },
            },
        ],
        "summary": {},
        "marketObservation": {"summary": {}, "chainSpecificObservationPairs": []},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {},
    }


def test_quote_routes_payload_anchor_filter_limits_routes_and_live_quote_calls(monkeypatch):
    monkeypatch.delenv("DEX_QUOTE_ROUTE_ANCHOR_FILTER", raising=False)
    calls = {"quote": 0, "size": 0}

    def route_quote_analysis_fn(route, price_map, chain_id):
        calls["quote"] += 1
        return {
            "steps": [{"fromSymbol": route["routeSymbols"][0]}],
            "complete": True,
            "requiresLiveQuoter": False,
            "adapterRequired": False,
            "livePriceVerified": True,
            "priceEvidenceSource": "chain_live_quote",
            "priceEvidenceMode": "live_verified",
            "executableCandidate": False,
            "chainSubmissionCandidate": False,
            "chainSubmissionPolicy": "live_framework_reuse",
            "netProfitUsd": None,
        }

    def route_size_analysis_fn(route, price_map, chain_id):
        calls["size"] += 1
        return {"rows": [{"size": 1}]}

    payload = build_quote_routes_payload_from_overview(
        _anchor_overview_with_two_routes(),
        chain_id=46630,
        protocols=["uniswap_v4"],
        route_quote_analysis_fn=route_quote_analysis_fn,
        route_size_analysis_fn=route_size_analysis_fn,
        route_live_test_policy=QuoteLiveTestPolicy(anchor="eth", live_route_limit=10),
    )

    assert [route["symbolPair"] for route in payload["routes"]] == ["ETH -> AAA -> BBB -> ETH"]
    assert payload["summary"]["unfilteredRouteCount"] == 2
    assert payload["summary"]["anchorFilter"] == "ETH"
    assert calls["quote"] == 1
    assert calls["size"] == 1


def test_quote_routes_payload_anchor_filter_uses_explicit_arg_over_env(monkeypatch):
    monkeypatch.setenv("DEX_QUOTE_ROUTE_ANCHOR_FILTER", "USDC")
    calls = {"quote": 0, "size": 0}

    def route_quote_analysis_fn(route, price_map, chain_id):
        calls["quote"] += 1
        return {"steps": [{"fromSymbol": route["routeSymbols"][0]}], "complete": True, "livePriceVerified": True}

    def route_size_analysis_fn(route, price_map, chain_id):
        calls["size"] += 1
        return {"rows": [{"size": 1}]}

    payload = build_quote_routes_payload_from_overview(
        _anchor_overview_with_two_routes(),
        chain_id=46630,
        protocols=["uniswap_v4"],
        route_quote_analysis_fn=route_quote_analysis_fn,
        route_size_analysis_fn=route_size_analysis_fn,
        route_live_test_policy=QuoteLiveTestPolicy(anchor="eth", live_route_limit=10),
    )

    assert [route["symbolPair"] for route in payload["routes"]] == ["ETH -> AAA -> BBB -> ETH"]
    assert payload["summary"]["anchorFilter"] == "ETH"
    assert calls["quote"] == 1


def test_quote_routes_payload_anchor_filter_env_default(monkeypatch):
    monkeypatch.setenv("DEX_QUOTE_ROUTE_ANCHOR_FILTER", "USDC")

    payload = build_quote_routes_payload_from_overview(
        _anchor_overview_with_two_routes(),
        chain_id=46630,
        protocols=["uniswap_v4"],
        route_quote_analysis_fn=lambda route, price_map, chain_id: {"complete": True},
        route_size_analysis_fn=lambda route, price_map, chain_id: {"rows": []},
    )

    assert [route["symbolPair"] for route in payload["routes"]] == ["USDC -> AAA -> BBB -> USDC"]
    assert payload["summary"]["anchorFilter"] == "USDC"
    assert payload["summary"]["unfilteredRouteCount"] == 2


def test_quote_route_policy_matches_anchor_when_route_symbols_are_absent():
    policy = QuoteLiveTestPolicy(anchor="eth")
    path = {
        "symbolPair": "ETH -> AAA -> BBB -> ETH",
        "overviewRoute": {
            "anchorBorrowSymbol": "ETH",
            "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
        },
    }

    assert policy.matches_anchor(path) is True


def test_quote_routes_payload_anchor_filter_supports_multiple_anchors():
    payload = build_quote_routes_payload_from_overview(
        _anchor_overview_with_two_routes(),
        chain_id=46630,
        protocols=["uniswap_v4"],
        route_quote_analysis_fn=lambda route, price_map, chain_id: {"complete": True},
        route_size_analysis_fn=lambda route, price_map, chain_id: {"rows": []},
        route_live_test_policy=QuoteLiveTestPolicy(anchor="usdc, ETH"),
    )

    assert {route["symbolPair"] for route in payload["routes"]} == {
        "ETH -> AAA -> BBB -> ETH",
        "USDC -> AAA -> BBB -> USDC",
    }
    assert payload["summary"]["anchorFilter"] == "ETH, USDC"


def test_quote_routes_payload_anchor_skips_unscoped_cache(monkeypatch):
    from market.binance_market import dex_surfaces

    old_cache = dict(dex_surfaces._DEX_SURFACE_CACHE)
    dex_surfaces._DEX_SURFACE_CACHE.clear()
    monkeypatch.delenv("DEX_QUOTE_ROUTE_ANCHOR_FILTER", raising=False)
    calls = {"build": 0}

    def fake_build(**kwargs):
        calls["build"] += 1
        return {
            "mode": "dex_quote_routes",
            "routes": [{"symbolPair": "ETH -> AAA -> BBB -> ETH"}],
            "summary": {"anchorFilter": kwargs.get("anchor"), "routeCount": 0},
        }

    monkeypatch.setattr(dex_surfaces, "_quote_routes_payload_for_robinhood_family", fake_build)
    try:
        first = dex_surfaces.dex_quote_routes_payload(
            chain_id=46630,
            protocols=["uniswap_v4"],
            use_static_observation=True,
        )
        second = dex_surfaces.dex_quote_routes_payload(
            chain_id=46630,
            protocols=["uniswap_v4"],
            use_static_observation=True,
        )
        third = dex_surfaces.dex_quote_routes_payload(
            chain_id=46630,
            protocols=["uniswap_v4"],
            use_static_observation=True,
            anchor="ETH",
        )
    finally:
        dex_surfaces._DEX_SURFACE_CACHE.clear()
        dex_surfaces._DEX_SURFACE_CACHE.update(old_cache)

    assert calls["build"] == 2
    assert third["summary"]["anchorFilter"] == "ETH"
    assert first["summary"]["anchorFilter"] is None
