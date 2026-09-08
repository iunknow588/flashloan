import pytest

from market.binance_market import quote_service
from market.binance_market import pool_candidates
from market.binance_market import dex_surfaces
from market.binance_market.candidate_pool_cache import store as candidate_pool_cache_store
from dex.intent_trade.chains.common.dex_candidate_tables import build_anchor_candidate_groups
from dex.intent_trade.chains.robinhood_testnet import market_observation as robinhood_testnet_market_observation
from market.strategy.arbitrage import ArbitrageConfig


def _write_json(path, payload):
    import json

    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolated_dex_surface_cache():
    old_cache = dict(dex_surfaces._DEX_SURFACE_CACHE)
    dex_surfaces._DEX_SURFACE_CACHE.clear()
    try:
        yield
    finally:
        dex_surfaces._DEX_SURFACE_CACHE.clear()
        dex_surfaces._DEX_SURFACE_CACHE.update(old_cache)


def _pool(pool_id: str, token0_symbol: str, token1_symbol: str, token0: str, token1: str, *, price_native: str, token0_amount: float, token1_amount: float, tvl_usd: float) -> dict:
    return {
        "pool": pool_id,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "3000",
        "token0": token0,
        "token1": token1,
        "token0Symbol": token0_symbol,
        "token1Symbol": token1_symbol,
        "balances": {"token0": token0_amount, "token1": token1_amount, "tvlUsd": tvl_usd},
        "market": {"priceNative": price_native, "priceUsd": "1"},
        "depthBand": {"level": "deep", "label": "deep"},
    }


def test_bootstrap_dex_surface_dependencies_walks_backwards(monkeypatch):
    from web.data_routes import dex_surfaces as web_dex_surfaces

    calls = []

    monkeypatch.setattr(web_dex_surfaces, "dex_pool_candidates_payload", lambda **kwargs: calls.append("candidates") or {})
    monkeypatch.setattr(web_dex_surfaces, "dex_market_observation_surface_payload", lambda **kwargs: calls.append("surface") or {})
    monkeypatch.setattr(web_dex_surfaces, "dex_overview_payload", lambda **kwargs: calls.append("overview") or {})
    monkeypatch.setattr(web_dex_surfaces, "dex_quote_routes_payload", lambda **kwargs: calls.append("quotes") or {})

    web_dex_surfaces._bootstrap_dex_surface_dependencies(
        chain_id=46630,
        protocols=["uniswap_v4"],
        flow_name="dex_submission_review",
    )

    assert calls == ["candidates", "surface", "overview"]


def test_submission_review_framework_payload_exposes_static_framework_label(monkeypatch):
    from web.data_routes import dex_surfaces as web_dex_surfaces

    monkeypatch.setattr(
        web_dex_surfaces,
        "dex_overview_payload",
        lambda **kwargs: {
            "routeFrameworkSource": "market_observation",
            "marketObservationSource": "cached_snapshot",
            "marketObservationSourceStage": "cached_snapshot",
            "decisionBasis": "demo",
            "summary": {
                "arbitragePathCount": 2,
                "marketObservationSequenceCount": 1,
                "observationAnchorCount": 1,
                "candidatePathCount": 1,
                "overviewChainSubmissionCandidateCount": 1,
            },
            "arbitragePaths": [{"pathText": "A -> B -> A", "quoteStatus": "live_verified"}],
            "marketObservation": {"summary": {"observationSequenceCount": 1}},
        },
    )

    payload = web_dex_surfaces._submission_review_framework_payload(chain_id=46630, protocols=["uniswap_v4"])

    assert payload["routeFrameworkSource"] == "market_observation"
    assert payload["routeFrameworkLabel"] == "静态框架"
    assert payload["marketObservationSource"] == "cached_snapshot"
    assert payload["marketObservationLabel"] == "静态框架"
    assert payload["marketObservationSourceStage"] == "cached_snapshot"
    assert payload["marketObservationSourceStageLabel"] == "缓存静态快照"
    assert payload["routeCount"] == 2
    assert payload["topRouteText"] == "A -> B -> A"


def test_dex_provenance_framework_label_helper_shares_static_mapping():
    from core.dex_provenance import build_price_evidence_payload, framework_label, price_evidence_label, price_evidence_mode_label

    assert framework_label("market_observation") == "静态框架"
    assert framework_label("cached_snapshot") == "静态框架"
    assert framework_label("candidate_pool_fallback") == "候选框架"
    assert framework_label("computed") == "链上重算"
    assert framework_label("custom_source") == "custom_source"
    assert price_evidence_label("static_reference_probe") == "静态探针"
    assert price_evidence_label("live_quoter_required") == "需要链上 Quoter"
    assert price_evidence_label("chain_live_quote") == "链上实时报价"
    assert price_evidence_label("cached_framework_live_confirmation") == "静态框架 + 实时复核"
    assert price_evidence_mode_label("reference_only") == "参考价"
    assert price_evidence_mode_label("live_verified") == "实时价格已验证"
    evidence_payload = build_price_evidence_payload(source="static_reference_probe", mode="reference_only")
    assert evidence_payload["priceEvidenceSourceLabel"] == "静态探针"
    assert evidence_payload["priceEvidenceModeLabel"] == "参考价"


def test_overview_profit_payload_exposes_price_evidence_labels():
    from market.binance_market.dex_surfaces import _overview_profit_from_analysis

    payload = _overview_profit_from_analysis(
        {
            "priceEvidenceSource": "static_reference_probe",
            "priceEvidenceMode": "reference_only",
        }
    )

    assert payload["priceEvidenceSourceLabel"] == "静态探针"
    assert payload["priceEvidenceModeLabel"] == "参考价"


def test_v4_pool_token_metadata_uses_live_decimals_fallback(monkeypatch):
    token_address = "0x" + "1" * 40
    monkeypatch.setenv("DEX_V4_LIVE_TOKEN_METADATA_ENABLED", "1")
    monkeypatch.setattr(
        dex_surfaces,
        "_v4_token_metadata_cache",
        lambda pool_manager: {token_address.lower(): {"symbol": "TEST", "decimals": None}},
    )
    monkeypatch.setattr(dex_surfaces, "_live_v4_token_decimals", lambda address, chain_id=None: 12)

    metadata = dex_surfaces._v4_pool_token_metadata(
        {
            "poolManager": "0x" + "3" * 40,
            "token0": token_address,
            "token0Symbol": "TEST",
            "token1": "0x" + "2" * 40,
            "token1Symbol": "TEST2",
        },
        0,
        chain_id=46630,
    )

    assert metadata["decimals"] == 12
    assert metadata["source"] == "chain_live_erc20_decimals"
    assert metadata["metadataOk"] is True


def test_market_observation_source_stage_labels_are_distinct_from_framework_labels():
    from core.dex_provenance import market_observation_source_stage_label

    assert market_observation_source_stage_label("cached_snapshot") == "缓存静态快照"
    assert market_observation_source_stage_label("generated_live_snapshot") == "实时生成快照"
    assert market_observation_source_stage_label("refreshed_live_snapshot") == "实时刷新快照"
    assert market_observation_source_stage_label("computed") == "链上重算"


def test_static_framework_labels_stay_separate_from_live_price_evidence_labels():
    from core.dex_provenance import build_price_evidence_payload, build_route_framework_payload

    route_framework = build_route_framework_payload(
        route_framework_source="market_observation",
        market_observation_source="cached_snapshot",
        market_observation_source_stage="cached_snapshot",
        decision_basis="demo",
    )
    price_evidence = build_price_evidence_payload(source="live_quoter_required", mode="live_required")

    assert route_framework["routeFrameworkLabel"] == route_framework["marketObservationLabel"]
    assert route_framework["routeFrameworkLabel"] != price_evidence["priceEvidenceSourceLabel"]
    assert route_framework["marketObservationSourceStageLabel"] != price_evidence["priceEvidenceSourceLabel"]
    assert route_framework["routeFrameworkLabel"] != price_evidence["priceEvidenceModeLabel"]


def test_robinhood_chain_market_observation_flow_prefers_static_snapshot(monkeypatch):
    from core.chain_flow_dispatcher import ChainFlowRequest
    from dex.intent_trade.chains.robinhood import dex_surfaces as robinhood_dex_surfaces

    calls = {"surface": 0, "overview": 0, "quotes": 0, "request": 0}

    req = ChainFlowRequest(
        flow_name="dex_overview",
        protocol="uniswap_v4",
        chain_id=46630,
        network="robinhood",
        global_chain_id="robinhood-46630",
        handler="intent_trade.chains.robinhood.dex_surfaces:dex_overview_flow",
        flow={"allowed_protocols": ["uniswap_v4"]},
        chain_config={},
    )

    monkeypatch.setattr(
        robinhood_dex_surfaces,
        "dex_market_observation_surface_payload",
        lambda **kwargs: calls.__setitem__("surface", calls["surface"] + 1) or {"mode": "dex_market_observation"},
    )
    monkeypatch.setattr(
        robinhood_dex_surfaces,
        "dex_overview_payload",
        lambda **kwargs: calls.__setitem__("overview", calls["overview"] + 1) or {"mode": "dex_overview"},
    )
    monkeypatch.setattr(
        robinhood_dex_surfaces,
        "dex_quote_routes_payload",
        lambda **kwargs: calls.__setitem__("quotes", calls["quotes"] + 1) or {"mode": "dex_quote_routes"},
    )
    monkeypatch.setattr(
        robinhood_dex_surfaces,
        "dex_quote_request_payload",
        lambda **kwargs: calls.__setitem__("request", calls["request"] + 1) or {"mode": "dex_quote_request"},
    )

    assert robinhood_dex_surfaces.market_observation_flow(request=req, protocols=["uniswap_v4"])["mode"] == "dex_market_observation"
    assert robinhood_dex_surfaces.dex_overview_flow(request=req, protocols=["uniswap_v4"])["mode"] == "dex_overview"
    assert robinhood_dex_surfaces.dex_quote_routes_flow(request=req, protocols=["uniswap_v4"])["mode"] == "dex_quote_routes"
    assert robinhood_dex_surfaces.dex_quote_request_flow(request=req, protocols=["uniswap_v4"], pair="A", venue="uniswap_v4")["mode"] == "dex_quote_request"
    assert calls == {"surface": 1, "overview": 1, "quotes": 1, "request": 1}


def test_dex_opportunity_scanner_warmup_uses_live_overview(monkeypatch):
    from runtime import dex_opportunity_scanner

    calls = {"overview": []}

    class FakeScanner:
        _lock = __import__("threading").Lock()
        _snapshots = {}

        def start(self, *args, **kwargs):
            return None

        def snapshot(self, *args, **kwargs):
            return {"snapshot": {}}

        def quote_payload_provider(self, *args, **kwargs):
            return {"summary": {"routeCount": 1}, "routes": [{"symbolPair": "A -> B -> A"}]}

        def trigger_context_provider(self, *args, **kwargs):
            return {"source": "test"}

    monkeypatch.setattr(dex_opportunity_scanner, "get_dex_opportunity_scanner", lambda: FakeScanner())
    monkeypatch.setattr(
        dex_opportunity_scanner,
        "dex_overview_payload",
        lambda **kwargs: calls["overview"].append(kwargs) or {"mode": "dex_overview"},
    )

    scanner = dex_opportunity_scanner.ensure_dex_opportunity_scanner_running(chain_id=46630, protocols=["uniswap_v4"])

    assert scanner is not None
    assert calls["overview"] and calls["overview"][0]["use_static_observation"] is False


def test_dex_market_observation_includes_bridge_closed_loop(monkeypatch):
    usdc = "0x" + "1" * 40
    weth = "0x" + "2" * 40
    dai = "0x" + "3" * 40
    pool_usdc_weth = _pool("0x" + "a" * 40, "USDC", "WETH", usdc, weth, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=2_000_000)
    pool_usdc_dai = _pool("0x" + "b" * 40, "USDC", "DAI", usdc, dai, price_native="1.0", token0_amount=1_000_000, token1_amount=1_000_000, tvl_usd=2_000_000)
    pool_weth_dai = _pool("0x" + "c" * 40, "WETH", "DAI", weth, dai, price_native="2500.0", token0_amount=400, token1_amount=1_000_000, tvl_usd=2_000_000)
    pair_reports = [
        {
            "symbolPair": "USDC:WETH",
            "tokenX": usdc,
            "tokenY": weth,
            "tokenXSymbol": "USDC",
            "tokenYSymbol": "WETH",
            "preferredPools": [pool_usdc_weth],
            "executionPools": [pool_usdc_weth],
            "strictQualityPools": [pool_usdc_weth],
        },
        {
            "symbolPair": "USDC:DAI",
            "tokenX": usdc,
            "tokenY": dai,
            "tokenXSymbol": "USDC",
            "tokenYSymbol": "DAI",
            "preferredPools": [pool_usdc_dai],
            "executionPools": [pool_usdc_dai],
            "strictQualityPools": [pool_usdc_dai],
        },
        {
            "symbolPair": "WETH:DAI",
            "tokenX": weth,
            "tokenY": dai,
            "tokenXSymbol": "WETH",
            "tokenYSymbol": "DAI",
            "preferredPools": [pool_weth_dai],
            "executionPools": [pool_weth_dai],
            "strictQualityPools": [pool_weth_dai],
        },
    ]
    payload = {
        "pairs": pair_reports,
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "anchorGroups": build_anchor_candidate_groups(pair_reports, anchor_symbols=["USDC"]),
    }
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)

    observation = dex_surfaces.dex_market_observation_payload(chain_id=46630, protocols=["uniswap_v3"])

    assert observation["summary"]["closedLoopRouteCount"] >= 1
    assert any(
        route["kind"] == "bridge_closed_loop"
        and route["anchorSymbol"] == "USDC"
        and route["closedLoop"] is True
        and route["pathSymbols"][0] == "USDC"
        and route["pathSymbols"][-1] == "USDC"
        and set(route["pathSymbols"][1:-1]) == {"WETH", "DAI"}
        for route in observation["closedLoopRoutes"]
    )


def test_dex_market_observation_surface_uses_static_snapshot_when_requested(monkeypatch):
    snapshot = {
        "exists": True,
        "path": "observation.json",
        "chain_id": 46630,
        "protocols": ["uniswap_v4"],
        "marketObservationSource": "generated_live_snapshot",
        "summary": {
            "observationAnchorCount": 2,
            "candidateObservationGroupCount": 1,
            "observationSequenceCount": 1,
            "closedLoopRouteCount": 1,
        },
        "observationAnchorSymbols": ["ETH", "USDC"],
        "borrowableSymbols": ["ETH", "USDC"],
        "candidateObservationGroups": [{"anchorSymbol": "ETH", "observationSequenceCount": 1, "closedLoopRouteCount": 1}],
        "observationSequences": [{"pathText": "ETH -> USDC -> ETH"}],
        "closedLoopRoutes": [{"pathText": "ETH -> USDC -> ETH"}],
        "chainSpecificObservationPairs": [],
        "chainSpecificObservationPools": [],
        "directPools": [],
        "directClosedLoopRoutes": [],
        "indirectRoutes": [],
        "unmatchedPools": [],
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630, "exists": True},
        "dexSettlement": {"uniswapx": {"supported": False}},
    }

    monkeypatch.setattr(dex_surfaces, "_cached_framework_surface_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(dex_surfaces, "load_market_observation_static", lambda **kwargs: snapshot)

    observation = dex_surfaces.dex_market_observation_surface_payload(
        chain_id=46630,
        protocols=["uniswap_v4"],
        use_static_observation=True,
    )

    assert observation["marketObservationSource"] == "cached_snapshot"
    assert observation["marketObservationSourceStage"] == "cached_snapshot"
    assert observation["summary"]["closedLoopRouteCount"] == 1
    assert observation["observationAnchorSymbols"] == ["ETH", "USDC"]


def test_dex_market_observation_links_yc_to_zc_when_yz_pair_exists(monkeypatch):
    usdc = "0x" + "1" * 40
    weth = "0x" + "2" * 40
    dai = "0x" + "3" * 40
    pool_usdc_weth = _pool("0x" + "a" * 40, "USDC", "WETH", usdc, weth, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=2_000_000)
    pool_weth_dai = _pool("0x" + "b" * 40, "WETH", "DAI", weth, dai, price_native="2500.0", token0_amount=400, token1_amount=1_000_000, tvl_usd=2_000_000)
    pool_dai_usdc = _pool("0x" + "c" * 40, "DAI", "USDC", dai, usdc, price_native="1.0", token0_amount=1_000_000, token1_amount=1_000_000, tvl_usd=2_000_000)
    yc_row = dex_surfaces._route_step_row(
        pool_usdc_weth,
        from_token={"symbol": "USDC", "address": usdc},
        to_token={"symbol": "WETH", "address": weth},
        direction="yc",
    )
    zc_row = dex_surfaces._route_step_row(
        pool_dai_usdc,
        from_token={"symbol": "DAI", "address": dai},
        to_token={"symbol": "USDC", "address": usdc},
        direction="zc",
    )
    payload = {
        "pairs": [
            {
                "symbolPair": "WETH:DAI",
                "tokenX": weth,
                "tokenY": dai,
                "tokenXSymbol": "WETH",
                "tokenYSymbol": "DAI",
                "preferredPools": [pool_weth_dai],
                "executionPools": [pool_weth_dai],
                "strictQualityPools": [pool_weth_dai],
            }
        ],
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "anchorGroups": [
            {
                "anchorSymbol": "USDC",
                "anchorAddresses": [usdc],
                "ycPools": [yc_row],
                "zcPools": [zc_row],
                "ycTokens": [{"symbol": "WETH", "address": weth, "poolCount": "1", "pools": [yc_row]}],
                "zcTokens": [{"symbol": "DAI", "address": dai, "poolCount": "1", "pools": [zc_row]}],
                "ycPoolCount": "1",
                "zcPoolCount": "1",
                "ycTokenCount": "1",
                "zcTokenCount": "1",
                "candidatePoolCount": "2",
            }
        ],
    }
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)

    observation = dex_surfaces.dex_market_observation_payload(chain_id=46630, protocols=["uniswap_v3"])

    assert observation["summary"]["observationSequenceCount"] == 1
    sequence = observation["observationSequences"][0]
    assert sequence["kind"] == "anchor_closed_loop_token_bridge"
    assert sequence["pathSymbols"] == ["USDC", "WETH", "DAI", "USDC"]
    assert [row["direction"] for row in sequence["routePools"]] == ["yc", "bridge", "zc"]
    assert observation["closedLoopRoutes"][0]["source"] == "yc_zc_bridge_observation"
    group = observation["candidateObservationGroups"][0]
    assert group["observationReachable"] is True
    assert group["observationState"]["status"] == "reachable"

    overview = dex_surfaces.dex_overview_payload(chain_id=46630, protocols=["uniswap_v3"])
    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=46630, protocols=["uniswap_v3"])

    assert overview["decisionBasis"] == "robinhood_testnet_market_observation_closed_loop"
    assert overview["arbitragePaths"][0]["overviewRoute"]["pathText"] == "USDC -> WETH -> DAI -> USDC"
    assert quotes["summary"]["pathSource"] == "market_observation"
    assert quotes["routes"][0]["symbolPair"] == "USDC -> WETH -> DAI -> USDC"


def test_dex_market_observation_uses_v4_index_for_shared_yz_bridge(monkeypatch):
    usdc = "0x" + "1" * 40
    weth = "0x" + "2" * 40
    dai = "0x" + "3" * 40
    pool_usdc_weth = _pool("0x" + "a" * 40, "USDC", "WETH", usdc, weth, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=2_000_000)
    pool_usdc_dai = _pool("0x" + "b" * 40, "USDC", "DAI", usdc, dai, price_native="1.0", token0_amount=1_000_000, token1_amount=1_000_000, tvl_usd=2_000_000)
    for pool in (pool_usdc_weth, pool_usdc_dai):
        pool["dex"] = "uniswap_v4"
        pool["protocol"] = "uniswap_v4"
    shared_weth = dex_surfaces._route_step_row(
        pool_usdc_weth,
        from_token={"symbol": "USDC", "address": usdc},
        to_token={"symbol": "WETH", "address": weth},
        direction="shared",
    )
    shared_dai = dex_surfaces._route_step_row(
        pool_usdc_dai,
        from_token={"symbol": "USDC", "address": usdc},
        to_token={"symbol": "DAI", "address": dai},
        direction="shared",
    )
    payload = {
        "pairs": [],
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "anchorGroups": [
            {
                "anchorSymbol": "USDC",
                "anchorAddresses": [usdc],
                "ycPools": [],
                "zcPools": [],
                "sharedPools": [shared_weth, shared_dai],
                "ycPoolsRef": "sharedPools",
                "zcPoolsRef": "sharedPools",
                "ycTokens": [],
                "zcTokens": [],
                "sharedTokens": [
                    {"symbol": "WETH", "address": weth, "poolCount": "1", "pools": [shared_weth]},
                    {"symbol": "DAI", "address": dai, "poolCount": "1", "pools": [shared_dai]},
                ],
                "ycTokensRef": "sharedTokens",
                "zcTokensRef": "sharedTokens",
                "ycZcSameSet": True,
                "ycPoolCount": "2",
                "zcPoolCount": "2",
                "ycTokenCount": "2",
                "zcTokenCount": "2",
                "candidatePoolCount": "2",
            }
        ],
    }
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)
    def load_v4_pool_index():
        return {
            "ok": True,
            "status": "v4_pool_index_available",
            "source": "test-index",
            "block_number": 123,
            "pools": [
                {
                    "poolId": "0x" + "c" * 64,
                    "protocol": "uniswap_v4",
                    "token0": weth,
                    "token1": dai,
                    "token0Symbol": "0x222222",
                    "token1Symbol": "0x333333",
                    "fee": 3000,
                    "poolKeyDecoded": True,
                }
            ],
        }

    monkeypatch.setattr(
        dex_surfaces,
        "robinhood_testnet_v4_bridge_pairs_for_anchor_groups",
        lambda anchor_groups, chain_id=None, protocols=None: robinhood_testnet_market_observation.robinhood_testnet_v4_bridge_pairs_for_anchor_groups(
            anchor_groups,
            chain_id=chain_id,
            protocols=protocols,
            pool_index_loader=load_v4_pool_index,
        ),
    )
    monkeypatch.setattr(
        dex_surfaces,
        "robinhood_testnet_v4_fund_anchor_pair_observation",
        lambda chain_id=None, protocols=None: {"enabled": True, "status": "test_disabled", "observationSequences": []},
    )

    observation = dex_surfaces.dex_market_observation_payload(chain_id=46630, protocols=["uniswap_v4"])

    assert observation["summary"]["bridgePoolIndexMatchedPairCount"] == 1
    assert observation["summary"]["observationSequenceCount"] >= 1
    assert any(sequence["pathSymbols"] == ["USDC", "WETH", "DAI", "USDC"] for sequence in observation["observationSequences"])
    bridge_pool = observation["observationSequences"][0]["bridgePool"]
    assert bridge_pool["protocol"] == "uniswap_v4"
    assert bridge_pool["source"] == "v4_pool_index_bridge_pool"


def test_robinhood_testnet_v4_non_eth_pair_first_links_native_fund_to_bridge_pair():
    zero = "0x" + "0" * 40
    token_y = "0x" + "4" * 40
    token_z = "0x" + "5" * 40

    def pool(pool_id, token0, token1, symbol0, symbol1):
        return {
            "poolId": pool_id,
            "token0": token0,
            "token1": token1,
            "token0Symbol": symbol0,
            "token1Symbol": symbol1,
            "fee": 3000,
            "poolKeyDecoded": True,
        }

    def load_v4_pool_index():
        return {
            "ok": True,
            "status": "v4_pool_index_available",
            "source": "test-index",
            "block_number": 123,
            "pools": [
                pool("0x" + "a" * 64, zero, token_y, "ETH", "AAA"),
                pool("0x" + "b" * 64, token_y, token_z, "AAA", "BBB"),
                pool("0x" + "c" * 64, zero, token_z, "ETH", "BBB"),
            ],
        }

    def load_v4_pool_liquidity():
        return {
            "ok": True,
            "status": "v4_pool_liquidity_cache_available",
            "source": "test-liquidity",
            "pool_manager": "0x" + "9" * 40,
            "payload": {
                "poolManager": "0x" + "9" * 40,
                "nativePoolManagerBalanceWei": "2000000000000000000",
            },
            "pools": [],
            "pool_keys": [],
        }

    observation = robinhood_testnet_market_observation.robinhood_testnet_v4_non_eth_pair_first_observation(
        chain_id=46630,
        protocols=["uniswap_v4"],
        pool_index_loader=load_v4_pool_index,
        pool_liquidity_loader=load_v4_pool_liquidity,
    )

    assert observation["nonEthPairCount"] == 1
    assert observation["associatedNonEthPairCount"] == 1
    assert observation["associatedObservationPairCount"] == 1
    assert observation["fundAnchorCandidatePairCount"] == 3
    assert observation["associatedObservationCandidatePairCount"] == 3
    # Observation detail is route-level: the bridge pool is retained here,
    # while entry/exit pools remain embedded in each route sequence.
    assert observation["associatedObservationPoolCount"] == 1
    assert observation["associatedObservationTokenCount"] == 2
    assert observation["associatedObservationPairs"][0]["symbolPair"] == "AAA:BBB"
    assert observation["associatedObservationPairs"][0]["poolCount"] == 1
    assert observation["associatedObservationPairs"][0]["directSwapRequired"] is True
    assert observation["associatedObservationPairs"][0]["directSwapPoolCount"] == 1
    assert observation["associatedObservationPairs"][0]["directSwapPools"][0]["role"] == "direct_y_z_swap_pool"
    assert observation["associatedObservationPairs"][0]["closedLoopReady"] is True
    assert set(observation["associatedObservationPairs"][0]["closedLoopRoutePreviews"]) == {
        "ETH -> AAA -> BBB -> ETH",
        "ETH -> BBB -> AAA -> ETH",
    }
    assert {
        tuple(step["direction"] for step in route["routePools"])
        for route in observation["associatedObservationPairs"][0]["routeEvidence"]
    } == {("yc", "bridge", "zc")}
    assert observation["routeCount"] == 2
    assert {sequence["pathText"] for sequence in observation["observationSequences"]} == {
        "ETH -> AAA -> BBB -> ETH",
        "ETH -> BBB -> AAA -> ETH",
    }
    assert all(sequence["kind"] == "robinhood_testnet_v4_fund_anchor_pair_observation" for sequence in observation["observationSequences"])


def test_robinhood_testnet_v4_associated_non_eth_pool_is_diagnostic_only_without_same_anchor_loop():
    fund_a = "0x" + "1" * 40
    fund_b = "0x" + "2" * 40
    token_y = "0x" + "4" * 40
    token_z = "0x" + "5" * 40

    def pool(pool_id, token0, token1, symbol0, symbol1):
        return {
            "poolId": pool_id,
            "token0": token0,
            "token1": token1,
            "token0Symbol": symbol0,
            "token1Symbol": symbol1,
            "fee": 3000,
            "poolKeyDecoded": True,
        }

    def load_v4_pool_index():
        return {
            "ok": True,
            "status": "v4_pool_index_available",
            "source": "test-index",
            "block_number": 123,
            "pools": [
                pool("0x" + "a" * 64, fund_a, token_y, "FUND_A", "AAA"),
                pool("0x" + "b" * 64, token_y, token_z, "AAA", "BBB"),
                pool("0x" + "c" * 64, token_z, fund_b, "BBB", "FUND_B"),
            ],
        }

    def load_v4_pool_liquidity():
        return {
            "ok": True,
            "status": "v4_pool_liquidity_cache_available",
            "source": "test-liquidity",
            "pool_manager": "0x" + "9" * 40,
            "payload": {
                "poolManager": "0x" + "9" * 40,
                "flashAccountingSources": [
                    {
                        "address": fund_a,
                        "symbol": "FUND_A",
                        "decimals": 18,
                        "poolManagerBalanceRaw": "1000000000000000000",
                        "poolManagerBalance": 1,
                    },
                    {
                        "address": fund_b,
                        "symbol": "FUND_B",
                        "decimals": 18,
                        "poolManagerBalanceRaw": "1000000000000000000",
                        "poolManagerBalance": 1,
                    },
                ],
            },
            "pools": [],
            "pool_keys": [],
        }

    observation = robinhood_testnet_market_observation.robinhood_testnet_v4_non_eth_pair_first_observation(
        chain_id=46630,
        protocols=["uniswap_v4"],
        pool_index_loader=load_v4_pool_index,
        pool_liquidity_loader=load_v4_pool_liquidity,
    )

    assert observation["associatedObservationCandidatePairCount"] == 3
    assert observation["associatedObservationPairCount"] == 0
    assert observation["associatedObservationPoolCount"] == 0
    assert observation["associatedObservationTokenCount"] == 0
    assert observation["associatedObservationPairs"] == []
    assert observation["routeCount"] == 0
    assert observation["associatedObservationPools"] == []
    assert observation["associatedObservationTokens"] == []


def test_robinhood_testnet_v4_fund_anchor_pair_allows_yz_to_include_other_fund_asset():
    fund_a = "0x" + "1" * 40
    fund_b = "0x" + "2" * 40
    token_y = "0x" + "4" * 40

    def pool(pool_id, token0, token1, symbol0, symbol1):
        return {
            "poolId": pool_id,
            "token0": token0,
            "token1": token1,
            "token0Symbol": symbol0,
            "token1Symbol": symbol1,
            "fee": 3000,
            "poolKeyDecoded": True,
        }

    def load_v4_pool_index():
        return {
            "ok": True,
            "status": "v4_pool_index_available",
            "source": "test-index",
            "block_number": 123,
            "pools": [
                pool("0x" + "a" * 64, fund_a, fund_b, "FUND_A", "FUND_B"),
                pool("0x" + "b" * 64, fund_b, token_y, "FUND_B", "AAA"),
                pool("0x" + "c" * 64, token_y, fund_a, "AAA", "FUND_A"),
            ],
        }

    def load_v4_pool_liquidity():
        return {
            "ok": True,
            "status": "v4_pool_liquidity_cache_available",
            "source": "test-liquidity",
            "pool_manager": "0x" + "9" * 40,
            "payload": {
                "poolManager": "0x" + "9" * 40,
                "flashAccountingSources": [
                    {
                        "address": fund_a,
                        "symbol": "FUND_A",
                        "decimals": 18,
                        "poolManagerBalanceRaw": "1000000000000000000",
                        "poolManagerBalance": 1,
                    },
                    {
                        "address": fund_b,
                        "symbol": "FUND_B",
                        "decimals": 18,
                        "poolManagerBalanceRaw": "1000000000000000000",
                        "poolManagerBalance": 1,
                    },
                ],
            },
            "pools": [],
            "pool_keys": [],
        }

    observation = robinhood_testnet_market_observation.robinhood_testnet_v4_fund_anchor_pair_observation(
        chain_id=46630,
        protocols=["uniswap_v4"],
        pool_index_loader=load_v4_pool_index,
        pool_liquidity_loader=load_v4_pool_liquidity,
    )

    assert observation["associatedObservationPairCount"] == 2
    assert {pair["symbolPair"] for pair in observation["associatedObservationPairs"]} == {
        "FUND_B:AAA",
        "AAA:FUND_A",
    }
    assert {sequence["pathText"] for sequence in observation["observationSequences"]} == {
        "FUND_A -> FUND_B -> AAA -> FUND_A",
        "FUND_A -> AAA -> FUND_B -> FUND_A",
        "FUND_B -> AAA -> FUND_A -> FUND_B",
        "FUND_B -> FUND_A -> AAA -> FUND_B",
    }
    assert all(
        sequence["anchorAddress"] not in {
            sequence["routePools"][1]["routeAddresses"][0],
            sequence["routePools"][1]["routeAddresses"][1],
        }
        for sequence in observation["observationSequences"]
    )


def test_dex_market_observation_keeps_class_when_no_yz_pair_exists(monkeypatch):
    usdc = "0x" + "1" * 40
    weth = "0x" + "2" * 40
    dai = "0x" + "3" * 40
    pool_usdc_weth = _pool("0x" + "a" * 40, "USDC", "WETH", usdc, weth, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=2_000_000)
    pool_dai_usdc = _pool("0x" + "c" * 40, "DAI", "USDC", dai, usdc, price_native="1.0", token0_amount=1_000_000, token1_amount=1_000_000, tvl_usd=2_000_000)
    yc_row = dex_surfaces._route_step_row(
        pool_usdc_weth,
        from_token={"symbol": "USDC", "address": usdc},
        to_token={"symbol": "WETH", "address": weth},
        direction="yc",
    )
    zc_row = dex_surfaces._route_step_row(
        pool_dai_usdc,
        from_token={"symbol": "DAI", "address": dai},
        to_token={"symbol": "USDC", "address": usdc},
        direction="zc",
    )
    payload = {
        "pairs": [],
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "anchorGroups": [
            {
                "anchorSymbol": "USDC",
                "anchorAddresses": [usdc],
                "ycPools": [yc_row],
                "zcPools": [zc_row],
                "ycTokens": [{"symbol": "WETH", "address": weth, "poolCount": "1", "pools": [yc_row]}],
                "zcTokens": [{"symbol": "DAI", "address": dai, "poolCount": "1", "pools": [zc_row]}],
                "ycPoolCount": "1",
                "zcPoolCount": "1",
                "ycTokenCount": "1",
                "zcTokenCount": "1",
                "candidatePoolCount": "2",
            }
        ],
    }
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)

    observation = dex_surfaces.dex_market_observation_payload(chain_id=46630, protocols=["uniswap_v3"])

    assert observation["summary"]["candidateObservationGroupCount"] == 1
    assert observation["summary"]["observationSequenceCount"] == 0
    group = observation["candidateObservationGroups"][0]
    assert group["anchorSymbol"] == "USDC"
    assert group["observationReachable"] is False
    assert group["observationState"]["status"] == "no_yz_pair"
    assert "Y-Z trading pair pool" in group["observationEmptyReason"]


def test_dex_market_observation_filters_same_symbol_reentry_without_profit(monkeypatch):
    usdc = "0x" + "1" * 40
    weth = "0x" + "2" * 40
    pool_a = _pool("0x" + "a" * 40, "USDC", "WETH", usdc, weth, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=2_000_000)
    pool_b = _pool("0x" + "b" * 40, "USDC", "WETH", usdc, weth, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=2_000_000)
    pair_reports = [
        {
            "symbolPair": "USDC:WETH",
            "tokenX": usdc,
            "tokenY": weth,
            "tokenXSymbol": "USDC",
            "tokenYSymbol": "WETH",
            "preferredPools": [pool_a, pool_b],
            "executionPools": [pool_a, pool_b],
            "strictQualityPools": [pool_a, pool_b],
        }
    ]
    payload = {
        "pairs": pair_reports,
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "anchorGroups": build_anchor_candidate_groups(pair_reports, anchor_symbols=["USDC"]),
    }
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)

    observation = dex_surfaces.dex_market_observation_payload(chain_id=46630, protocols=["uniswap_v3"])

    assert all(route["kind"] != "direct_closed_pair" for route in observation["closedLoopRoutes"])
    assert all(sequence["kind"] != "anchor_closed_loop_reentry" for sequence in observation["observationSequences"])


