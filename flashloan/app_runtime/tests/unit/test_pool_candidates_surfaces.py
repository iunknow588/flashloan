import pytest

from market.binance_market import quote_service
from market.binance_market import pool_candidates
from market.binance_market import dex_surfaces
from market.binance_market.dex_surface_v4 import _v4_spot_rate
from market.binance_market.candidate_pool_cache import store as candidate_pool_cache_store
from dex.intent_trade.chains.common.dex_candidate_tables import build_anchor_candidate_groups
from dex.intent_trade.chains.robinhood_testnet import market_observation as robinhood_testnet_market_observation
from market.strategy.arbitrage import ArbitrageConfig


def _write_json(path, payload):
    import json

    path.write_text(json.dumps(payload), encoding="utf-8")


def test_v4_spot_rate_does_not_assume_missing_token_decimals():
    pool = {
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "token0": "0x" + "0" * 40,
        "token1": "0x" + "2" * 40,
        "token0Symbol": "ETH",
        "token1Symbol": "TKN",
        "sqrtPriceX96": str(2**96),
    }
    assert _v4_spot_rate(pool, "ETH", "TKN") is None


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
        flow_name="dex_quote_request",
    )

    assert calls == ["candidates", "overview", "quotes"]


def test_bootstrap_dex_market_observation_skips_pool_candidates_warmup(monkeypatch):
    from web.data_routes import dex_surfaces as web_dex_surfaces

    calls = []

    monkeypatch.setattr(web_dex_surfaces, "dex_pool_candidates_payload", lambda **kwargs: calls.append("candidates") or {})
    monkeypatch.setattr(web_dex_surfaces, "dex_market_observation_surface_payload", lambda **kwargs: calls.append("surface") or {})

    web_dex_surfaces._bootstrap_dex_surface_dependencies(
        chain_id=46630,
        protocols=["uniswap_v4"],
        flow_name="dex_market_observation",
    )

    assert calls == []


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


def test_market_observation_page_payload_reuses_cached_page_snapshot(monkeypatch):
    from flask import Flask
    from web.data_routes.dex_surface_orchestration import _market_observation_page_payload
    from web.data_routes import dex_surface_orchestration as orchestration

    payload = {"chainSpecificObservationPools": list(range(200)), "observationSequences": list(range(10))}
    monkeypatch.setattr(orchestration, "_cached_surface_payload", lambda *a, **kw: {"from_cache": True})

    app = Flask(__name__)
    with app.test_request_context("/api/dex/market-observation?chain_id=4663&protocols=uniswap_v4"):
        result = _market_observation_page_payload(
            payload,
            chain_id=4663,
            protocols=["uniswap_v4"],
        )

    assert result == {"from_cache": True}


def test_dex_opportunity_scanner_warmup_uses_static_overview_for_robinhood_family(monkeypatch):
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
    assert calls["overview"] and calls["overview"][0]["use_static_observation"] is True


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

    assert observation["summary"]["closedLoopRouteCount"] == 0
    usdc_group = next(
        group
        for group in observation["candidateObservationGroups"]
        if group.get("anchorSymbol") == "USDC"
    )
    assert int(usdc_group.get("ycTokenCount") or 0) > 0
    assert int(usdc_group.get("zcTokenCount") or 0) > 0

    overview = dex_surfaces.dex_overview_payload(chain_id=46630, protocols=["uniswap_v3"])
    assert overview["summary"]["arbitragePathCount"] >= 1
    assert any(
        (path.get("overviewRoute") or {}).get("pathSymbols") == ["USDC", "WETH", "DAI", "USDC"]
        or (path.get("overviewRoute") or {}).get("pathSymbols") == ["USDC", "DAI", "WETH", "USDC"]
        for path in overview["arbitragePaths"]
    )


def test_dex_market_observation_collapses_reverse_closed_loop_directions(monkeypatch):
    def _step(pool_id, source, target, direction):
        return {
            "pool": pool_id,
            "poolId": pool_id,
            "direction": direction,
            "anchorSymbol": source,
            "counterSymbol": target,
            "routeSymbols": [source, target],
            "routeAddresses": ["0x" + ("1" * 40), "0x" + ("2" * 40)],
            "routeText": f"{source} -> {target}",
            "pairSymbol": f"{source}:{target}",
            "pairAddress": "0x1:0x2",
            "balances": {},
            "evidence": {},
        }

    def _sequence(path_text, bridge, counter):
        symbols = path_text.split(" -> ")
        return {
            "anchorSymbol": "TZ",
            "bridgeSymbol": bridge,
            "counterSymbol": counter,
            "pathSymbols": symbols,
            "pathText": path_text,
            "routePools": [
                _step("0x" + ("a" * 64), symbols[0], symbols[1], "yc"),
                _step("0x" + ("b" * 64), symbols[1], symbols[2], "bridge"),
                _step("0x" + ("c" * 64), symbols[2], symbols[3], "zc"),
            ],
            "kind": "robinhood_mainnet_eth_anchor_closed_loop",
            "source": "test_reverse_closed_loop",
        }

    monkeypatch.setattr(
        dex_surfaces,
        "dex_pool_candidates_payload",
        lambda chain_id=None, protocols=None: {
            "pairs": [],
            "summary": {},
            "dexSettlement": {},
            "poolStaticTable": {},
            "anchorGroups": [],
        },
    )
    monkeypatch.setattr(
        dex_surfaces,
        "_chain_fund_anchor_pair_observation",
        lambda chain_id=None, protocols=None: {
            "enabled": True,
            "status": "test",
            "observationSequences": [
                _sequence("TZ -> TB -> TA -> TZ", "TB", "TA"),
                _sequence("TZ -> TA -> TB -> TZ", "TA", "TB"),
            ],
            "associatedObservationPairs": [],
            "associatedObservationPools": [],
            "associatedObservationTokens": [],
        },
    )
    monkeypatch.setattr(
        dex_surfaces,
        "_v4_index_bridge_pairs_for_anchor_groups",
        lambda *args, **kwargs: ([], {"enabled": False, "status": "test_stub"}),
    )

    observation = dex_surfaces.dex_market_observation_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
    )

    assert len(observation["observationSequences"]) == 0
    assert len(observation["closedLoopRoutes"]) == 0


def test_dex_market_observation_tz_shared_bridge_pool_is_one_combination(monkeypatch):
    tz = "0x" + "1" * 40
    tb = "0x" + "2" * 40
    ta = "0x" + "3" * 40
    bridge_pool_id = "0xe992d4e9041b58b363a83c1863a664a7c07e46728ee6788a38f789102bff83a0"
    pool_tz_tb = _pool(
        "0x" + "4" * 64,
        "TZ",
        "TB",
        tz,
        tb,
        price_native="1.0",
        token0_amount=1000,
        token1_amount=1000,
        tvl_usd=1000,
    )
    pool_tz_ta = _pool(
        "0x" + "5" * 64,
        "TZ",
        "TA",
        tz,
        ta,
        price_native="1.0",
        token0_amount=1000,
        token1_amount=1000,
        tvl_usd=1000,
    )
    pool_tb_ta = _pool(
        bridge_pool_id,
        "TB",
        "TA",
        tb,
        ta,
        price_native="1.0",
        token0_amount=1000,
        token1_amount=1000,
        tvl_usd=1000,
    )
    for pool in (pool_tz_tb, pool_tz_ta, pool_tb_ta):
        pool["hasLiquidity"] = True

    def pair(pool, x_symbol, y_symbol, x_address, y_address):
        return {
            "symbolPair": f"{x_symbol}:{y_symbol}",
            "tokenX": x_address,
            "tokenY": y_address,
            "tokenXSymbol": x_symbol,
            "tokenYSymbol": y_symbol,
            "preferredPools": [pool],
            "executionPools": [pool],
            "strictQualityPools": [pool],
        }

    payload = {
        "pairs": [
            pair(pool_tz_tb, "TZ", "TB", tz, tb),
            pair(pool_tz_ta, "TZ", "TA", tz, ta),
        ],
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "anchorGroups": build_anchor_candidate_groups(
            [
                pair(pool_tz_tb, "TZ", "TB", tz, tb),
                pair(pool_tz_ta, "TZ", "TA", tz, ta),
            ],
            anchor_symbols=["TZ"],
        ),
    }
    bridge_pair = pair(pool_tb_ta, "TB", "TA", tb, ta)
    bridge_pair["addressPair"] = f"{tb}:{ta}"
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)
    monkeypatch.setattr(
        dex_surfaces,
        "_v4_index_bridge_pairs_for_anchor_groups",
        lambda anchor_groups, chain_id=None, protocols=None: (
            [bridge_pair],
            {"enabled": True, "status": "test", "matchedPoolCount": 1},
        ),
    )
    monkeypatch.setattr(
        dex_surfaces,
        "_chain_fund_anchor_pair_observation",
        lambda chain_id=None, protocols=None: {
            "enabled": True,
            "status": "test",
            "observationSequences": [],
            "associatedObservationPairs": [],
            "associatedObservationPools": [],
            "associatedObservationTokens": [],
        },
    )

    observation = dex_surfaces.dex_market_observation_payload(
        chain_id=46630,
        protocols=["uniswap_v4"],
    )

    tz_group = next(
        group
        for group in observation["candidateObservationGroups"]
        if group.get("anchorSymbol") == "TZ"
    )
    assert {sequence["pathText"] for sequence in tz_group["observationSequences"]} == {
        "TZ -> TB -> TA -> TZ",
        "TZ -> TA -> TB -> TZ",
    }
    assert tz_group["observationSequenceCount"] == 1
    assert tz_group["observationCombinationCount"] == 1
    bridge_groups = tz_group["bridgePoolObservationGroups"]
    assert len(bridge_groups) == 1
    assert bridge_groups[0]["bridgePool"] == bridge_pool_id
    assert bridge_groups[0]["directionCount"] == 2
    assert bridge_groups[0]["preview"] == "TZ -> TB -> TA -> TZ"


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
            "closedLoopRouteCount": 0,
        },
        "observationAnchorSymbols": ["ETH", "USDC"],
        "borrowableSymbols": ["ETH", "USDC"],
        "candidateObservationGroups": [{"anchorSymbol": "ETH", "observationSequenceCount": 1, "closedLoopRouteCount": 1}],
        "observationSequences": [{"pathText": "ETH -> USDC -> DAI -> ETH"}],
        "closedLoopRoutes": [{"pathText": "ETH -> USDC -> DAI -> ETH"}],
        "chainSpecificObservationPairs": [],
        "chainSpecificObservationPools": [],
        "directPools": [],
        "indirectRoutes": [],
        "unmatchedPools": [],
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630, "exists": True},
        "dexSettlement": {"uniswapx": {"supported": False}},
    }

    monkeypatch.setattr(dex_surfaces, "_cached_framework_surface_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(dex_surfaces, "load_market_observation_static", lambda **kwargs: snapshot)
    monkeypatch.setattr(
        dex_surfaces,
        "_database_first_v4_robinhood",
        lambda *, chain_id=None, protocols=None: False,
    )

    observation = dex_surfaces.dex_market_observation_surface_payload(
        chain_id=46630,
        protocols=["uniswap_v4"],
        use_static_observation=True,
    )

    assert observation["marketObservationSource"] == "cached_snapshot"
    assert observation["marketObservationSourceStage"] == "cached_snapshot"
    assert observation["summary"]["closedLoopRouteCount"] == 0
    assert observation["observationAnchorSymbols"] == ["ETH", "USDC"]


def test_dex_market_observation_surface_uses_database_for_mainnet_v4(monkeypatch):
    static_calls = []

    def fail_static(**kwargs):
        static_calls.append(kwargs)
        return {"exists": True, "path": "observation.json"}

    monkeypatch.setattr(dex_surfaces, "_cached_framework_surface_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(dex_surfaces, "load_market_observation_static", fail_static)
    monkeypatch.setattr(
        dex_surfaces,
        "_database_first_robinhood_observation_payload",
        lambda chain_id=None, protocols=None: {
            "mode": "dex_market_observation",
            "chain_id": chain_id,
            "selected_protocols": protocols,
            "marketObservationSource": "sqlite_db",
            "marketObservationSourceStage": "sqlite_db",
            "summary": {"observationSequenceCount": 1},
            "observationSequences": [{"pathText": "ETH -> USDC -> DAI -> ETH"}],
        },
    )

    observation = dex_surfaces.dex_market_observation_surface_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        use_static_observation=True,
    )

    assert observation["marketObservationSource"] == "sqlite_db"
    assert observation["marketObservationSourceStage"] == "sqlite_db"
    assert static_calls == []


def test_database_overview_snapshot_backs_mainnet_v4_static_contracts(monkeypatch):
    overview = {
        "chain_id": 4663,
        "selected_protocols": ["uniswap_v4"],
        "summary": {"closedLoopAnchorSymbols": ["ETH"]},
        "borrowableSymbols": ["ETH"],
        "arbitragePaths": [
            {
                "routeSymbols": ["ETH", "USDC", "DAI", "ETH"],
                "routeAddresses": ["0x0", "0x1", "0x2", "0x0"],
                "overviewRoute": {
                    "anchorBorrowSymbol": "ETH",
                    "targetSymbol": "DAI",
                    "pathSymbols": ["ETH", "USDC", "DAI", "ETH"],
                    "pathAddresses": ["0x0", "0x1", "0x2", "0x0"],
                    "pathText": "ETH -> USDC -> DAI -> ETH",
                },
                "executionPools": [
                    {
                        "pool": "0x" + "a" * 64,
                        "poolId": "0x" + "a" * 64,
                        "fee": 3000,
                        "hasLiquidity": True,
                        "usableForObservation": True,
                    }
                ],
            }
        ],
        "liquidPoolRegistry": {},
        "poolIndexVersion": {"source": "sqlite_db"},
    }
    monkeypatch.setattr(
        dex_surfaces,
        "dex_overview_payload",
        lambda chain_id=None, protocols=None, use_static_observation=False: dict(overview),
    )

    snapshot = dex_surfaces.load_observation_overview_for_scope(
        chain_id=4663,
        protocols=["uniswap_v4"],
    )

    assert snapshot["exists"] is True
    assert snapshot["path"] == "sqlite_db"
    assert len(snapshot["closedLoopRouteSummaries"]) == 1
    assert snapshot["closedLoopRouteSummaries"][0]["anchorSymbol"] == "ETH"
    assert snapshot["poolRegistry"]["0x" + "a" * 64]["hasLiquidity"] is True


def test_dex_market_observation_surface_recomputes_when_static_snapshot_is_empty(monkeypatch):
    empty_snapshot = {
        "exists": True,
        "path": "observation.json",
        "chain_id": 46630,
        "protocols": ["uniswap_v4"],
        "marketObservationSource": "cached_snapshot",
        "summary": {
            "observationAnchorCount": 0,
            "candidateObservationGroupCount": 0,
            "observationSequenceCount": 0,
            "closedLoopRouteCount": 0,
        },
        "observationAnchorSymbols": [],
        "borrowableSymbols": [],
        "candidateObservationGroups": [],
        "observationSequences": [],
        "closedLoopRoutes": [],
        "chainSpecificObservationPairs": [],
        "chainSpecificObservationPools": [],
        "directPools": [],
        "indirectRoutes": [],
        "unmatchedPools": [],
    }
    computed = {
        "mode": "dex_market_observation",
        "chain_id": 46630,
        "selected_protocols": ["uniswap_v4"],
        "observationAnchorSymbols": ["TZ", "TA", "TB"],
        "summary": {
            "observationAnchorCount": 3,
            "candidateObservationGroupCount": 1,
            "observationSequenceCount": 2,
            "closedLoopRouteCount": 0,
        },
        "candidateObservationGroups": [{"anchorSymbol": "TZ", "observationSequenceCount": 1}],
        "observationSequences": [{"anchorSymbol": "TZ"}],
    }

    monkeypatch.setattr(dex_surfaces, "_cached_framework_surface_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(dex_surfaces, "load_market_observation_static", lambda **kwargs: empty_snapshot)
    monkeypatch.setattr(
        dex_surfaces,
        "_database_first_robinhood_observation_payload",
        lambda chain_id=None, protocols=None: dict(computed),
    )
    computed["marketObservationSource"] = "sqlite_db"
    computed["marketObservationSourceStage"] = "sqlite_db"

    observation = dex_surfaces.dex_market_observation_surface_payload(
        chain_id=46630,
        protocols=["uniswap_v4"],
        use_static_observation=True,
    )

    assert observation["observationAnchorSymbols"] == ["TZ", "TA", "TB"]
    assert observation["summary"]["observationSequenceCount"] == 2
    assert observation["marketObservationSourceStage"] == "sqlite_db"


def test_dex_market_observation_links_yc_to_zc_when_yz_pair_exists(monkeypatch):
    usdc = "0x" + "1" * 40
    weth = "0x" + "2" * 40
    dai = "0x" + "3" * 40
    pool_usdc_weth = _pool("0x" + "a" * 40, "USDC", "WETH", usdc, weth, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=2_000_000)
    pool_weth_dai = _pool("0x" + "b" * 40, "WETH", "DAI", weth, dai, price_native="2500.0", token0_amount=400, token1_amount=1_000_000, tvl_usd=2_000_000)
    pool_dai_usdc = _pool("0x" + "c" * 40, "DAI", "USDC", dai, usdc, price_native="1.0", token0_amount=1_000_000, token1_amount=1_000_000, tvl_usd=2_000_000)
    for pool in (pool_usdc_weth, pool_weth_dai, pool_dai_usdc):
        pool["hasLiquidity"] = True
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
    assert len(observation["observationSequences"]) == 1
    assert observation["closedLoopRoutes"] == []
    group = observation["candidateObservationGroups"][0]
    assert group["observationState"]["status"] == "reachable"
    assert group["observationSequenceCount"] == 1

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

    def load_v4_pool_liquidity():
        return {
            "ok": True,
            "status": "v4_pool_liquidity_cache_available",
            "source": "test-liquidity",
            "pools": [
                {
                    "poolId": "0x" + "c" * 64,
                    "hasLiquidity": True,
                    "usableForObservation": True,
                }
            ],
            "pool_keys": [],
        }

    monkeypatch.setattr(
        dex_surfaces,
        "robinhood_testnet_v4_bridge_pairs_for_anchor_groups",
        lambda anchor_groups, chain_id=None, protocols=None: robinhood_testnet_market_observation.robinhood_testnet_v4_bridge_pairs_for_anchor_groups(
            anchor_groups,
            chain_id=chain_id,
            protocols=protocols,
            pool_index_loader=load_v4_pool_index,
            pool_liquidity_loader=load_v4_pool_liquidity,
        ),
    )
    monkeypatch.setattr(
        dex_surfaces,
        "robinhood_testnet_v4_fund_anchor_pair_observation",
        lambda chain_id=None, protocols=None: {"enabled": True, "status": "test_disabled", "observationSequences": []},
    )

    observation = dex_surfaces.dex_market_observation_payload(chain_id=46630, protocols=["uniswap_v4"])

    assert observation["summary"]["bridgePoolIndexMatchedPairCount"] == 1
    assert observation["summary"]["observationSequenceCount"] == 2
    assert len(observation["observationSequences"]) == 2
    group = observation["candidateObservationGroups"][0]
    assert group["observationSequenceCount"] == 1
    assert group["observationCombinationCount"] == 1
    assert len(group.get("bridgePoolObservationGroups") or []) == 1
    assert (group.get("bridgePoolObservationGroups") or [{}])[0]["directionCount"] == 2
    db_observation = {
        "mode": "dex_market_observation",
        "chain_id": 46630,
        "selected_protocols": ["uniswap_v4"],
        "marketObservationSource": "sqlite_db",
        "marketObservationSourceStage": "sqlite_db",
        "observationAnchorSymbols": ["USDC"],
        "anchorGroups": [
            {
                "anchorSymbol": "USDC",
                "observationSequences": [
                    {
                        "kind": "robinhood_database_closed_loop",
                        "source": "v4_closed_loop_db",
                        "anchorSymbol": "USDC",
                        "pathSymbols": ["USDC", "WETH", "DAI", "USDC"],
                        "pathText": "USDC -> WETH -> DAI -> USDC",
                        "hopCandidates": [],
                        "matchedPoolCount": "3",
                        "recommendedFirstPriority": True,
                    }
                ],
            }
        ],
        "closedLoopRoutes": [
            {
                "kind": "bridge_closed_loop",
                "closedLoop": True,
                "anchorSymbol": "USDC",
                "pathSymbols": ["USDC", "WETH", "DAI", "USDC"],
                "pathText": "USDC -> WETH -> DAI -> USDC",
                "routeTokenPath": [
                    {"symbol": "USDC", "address": usdc},
                    {"symbol": "WETH", "address": weth},
                    {"symbol": "DAI", "address": dai},
                    {"symbol": "USDC", "address": usdc},
                ],
                "routeAddresses": [usdc, weth, dai, usdc],
                "routePools": [
                    {
                        "pool": "0x" + "a" * 40,
                        "protocol": "uniswap_v4",
                        "fee": 100,
                        "routeSymbols": ["USDC", "WETH"],
                        "routeAddresses": [usdc, weth],
                    },
                    {
                        "pool": "0x" + "b" * 40,
                        "protocol": "uniswap_v4",
                        "fee": 30,
                        "routeSymbols": ["WETH", "DAI"],
                        "routeAddresses": [weth, dai],
                    },
                    {
                        "pool": "0x" + "c" * 40,
                        "protocol": "uniswap_v4",
                        "fee": 5,
                        "routeSymbols": ["DAI", "USDC"],
                        "routeAddresses": [dai, usdc],
                    },
                ],
                "routePairs": [],
            }
        ],
        "pairs": [],
        "bridgePairs": [],
        "summary": {"observationAnchorCount": "1", "closedLoopRouteCount": "1"},
    }
    monkeypatch.setattr(
        dex_surfaces,
        "_database_first_robinhood_observation_payload",
        lambda **kwargs: db_observation,
    )
    overview = dex_surfaces.dex_overview_payload(chain_id=46630, protocols=["uniswap_v4"])
    assert any(
        (path.get("overviewRoute") or {}).get("pathSymbols") == ["USDC", "WETH", "DAI", "USDC"]
        for path in overview["arbitragePaths"]
    )


def test_dex_market_observation_excludes_v4_yz_bridge_without_liquidity_evidence(monkeypatch):
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

    def load_v4_pool_liquidity():
        return {
            "ok": True,
            "status": "v4_pool_liquidity_cache_available",
            "source": "test-liquidity",
            "pools": [
                {
                    "poolId": "0x" + "9" * 64,
                    "hasLiquidity": True,
                    "usableForObservation": True,
                }
            ],
            "pool_keys": [],
        }

    monkeypatch.setattr(
        dex_surfaces,
        "robinhood_testnet_v4_bridge_pairs_for_anchor_groups",
        lambda anchor_groups, chain_id=None, protocols=None: robinhood_testnet_market_observation.robinhood_testnet_v4_bridge_pairs_for_anchor_groups(
            anchor_groups,
            chain_id=chain_id,
            protocols=protocols,
            pool_index_loader=load_v4_pool_index,
            pool_liquidity_loader=load_v4_pool_liquidity,
        ),
    )
    monkeypatch.setattr(
        dex_surfaces,
        "robinhood_testnet_v4_fund_anchor_pair_observation",
        lambda chain_id=None, protocols=None: {"enabled": True, "status": "test_disabled", "observationSequences": []},
    )

    observation = dex_surfaces.dex_market_observation_payload(chain_id=46630, protocols=["uniswap_v4"])

    assert observation["summary"]["bridgePoolIndexMatchedPairCount"] == 0
    assert observation["summary"]["observationSequenceCount"] == 2
    assert observation["closedLoopRoutes"] == []


def test_robinhood_testnet_v4_non_eth_pair_first_keeps_native_zero_fund_routes():
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
    assert observation["associatedObservationPoolCount"] == 1
    assert observation["associatedObservationTokenCount"] == 2
    assert observation["routeCount"] == 2
    assert observation["fundAssetCount"] == 1
    assert observation["executorSupportedFundAssetCount"] == 0
    assert [seq["pathText"] for seq in observation["observationSequences"]] == [
        "ETH -> AAA -> BBB -> ETH",
        "ETH -> BBB -> AAA -> ETH",
    ]


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
    for pair in observation["associatedObservationPairs"]:
        assert pair["directSwapPools"]
        direct_pool = pair["directSwapPools"][0]
        assert direct_pool["symbolPair"] == pair["symbolPair"]
        assert direct_pool["token0Symbol"]
        assert direct_pool["token1Symbol"]
        assert direct_pool["addressPair"]
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
    assert observation["summary"]["observationSequenceCount"] == 1
    group = observation["candidateObservationGroups"][0]
    assert group["anchorSymbol"] == "USDC"
    assert group["observationReachable"] is False
    assert group["observationState"]["status"] == "no_yz_pool"
    assert group["noPoolCombinationCount"] == 1
    assert group["candidateCombinationCount"] == 1


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

    assert all(route["kind"] == "bridge_closed_loop" for route in observation["closedLoopRoutes"])
    assert all(route.get("routeSemantics") == "x_y_entry_y_z_bridge_z_x_exit" for route in observation["closedLoopRoutes"])
    assert all(sequence["kind"] != "anchor_closed_loop_reentry" for sequence in observation["observationSequences"])


def test_dex_market_observation_preserves_two_candidate_pools_per_hop(monkeypatch):
    usdc = "0x" + "1" * 40
    weth = "0x" + "2" * 40
    dai = "0x" + "3" * 40
    deep_ids = ["0x" + "a" * 40, "0x" + "d" * 40, "0x" + "f" * 40]
    shallow_ids = ["0x" + "b" * 40, "0x" + "e" * 40, "0x" + "g" * 40]
    pool_usdc_weth_deep = _pool(deep_ids[0], "USDC", "WETH", usdc, weth, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=2_000_000)
    pool_usdc_weth_shallow = _pool(shallow_ids[0], "USDC", "WETH", usdc, weth, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=1_000_000)
    pool_usdc_dai_deep = _pool(deep_ids[1], "USDC", "DAI", usdc, dai, price_native="1.0", token0_amount=1_000_000, token1_amount=1_000_000, tvl_usd=2_000_000)
    pool_usdc_dai_shallow = _pool(shallow_ids[1], "USDC", "DAI", usdc, dai, price_native="1.0", token0_amount=1_000_000, token1_amount=1_000_000, tvl_usd=1_000_000)
    pool_weth_dai_deep = _pool(deep_ids[2], "WETH", "DAI", weth, dai, price_native="2500.0", token0_amount=400, token1_amount=1_000_000, tvl_usd=2_000_000)
    pool_weth_dai_shallow = _pool(shallow_ids[2], "WETH", "DAI", weth, dai, price_native="2500.0", token0_amount=400, token1_amount=1_000_000, tvl_usd=1_000_000)
    for pool in (
        pool_usdc_weth_deep,
        pool_usdc_weth_shallow,
        pool_usdc_dai_deep,
        pool_usdc_dai_shallow,
        pool_weth_dai_deep,
        pool_weth_dai_shallow,
    ):
        pool["hasLiquidity"] = True
        pool["usableForObservation"] = True
    pair_reports = [
        {
            "symbolPair": "USDC:WETH",
            "tokenX": usdc,
            "tokenY": weth,
            "tokenXSymbol": "USDC",
            "tokenYSymbol": "WETH",
            "preferredPools": [pool_usdc_weth_deep, pool_usdc_weth_shallow],
            "executionPools": [pool_usdc_weth_deep, pool_usdc_weth_shallow],
            "strictQualityPools": [pool_usdc_weth_deep, pool_usdc_weth_shallow],
        },
        {
            "symbolPair": "USDC:DAI",
            "tokenX": usdc,
            "tokenY": dai,
            "tokenXSymbol": "USDC",
            "tokenYSymbol": "DAI",
            "preferredPools": [pool_usdc_dai_deep, pool_usdc_dai_shallow],
            "executionPools": [pool_usdc_dai_deep, pool_usdc_dai_shallow],
            "strictQualityPools": [pool_usdc_dai_deep, pool_usdc_dai_shallow],
        },
        {
            "symbolPair": "WETH:DAI",
            "tokenX": weth,
            "tokenY": dai,
            "tokenXSymbol": "WETH",
            "tokenYSymbol": "DAI",
            "preferredPools": [pool_weth_dai_deep, pool_weth_dai_shallow],
            "executionPools": [pool_weth_dai_deep, pool_weth_dai_shallow],
            "strictQualityPools": [pool_weth_dai_deep, pool_weth_dai_shallow],
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
    assert observation["summary"]["closedLoopRouteCount"] == 0

    target_path = "USDC -> WETH -> DAI -> USDC"
    overview = dex_surfaces.dex_overview_payload(chain_id=46630, protocols=["uniswap_v3"])
    overview_path = next(
        path
        for path in overview["arbitragePaths"]
        if (path.get("overviewRoute") or {}).get("pathText") == target_path
    )
    assert len(overview_path["hopCandidates"]) == 3
    assert [candidate["poolCount"] for candidate in overview_path["hopCandidates"]] == [2, 2, 2]
    assert overview_path["overviewRoute"]["hopCandidateCombinationCount"] == 8
    preferred_by_leg = {
        candidate["leg"]: candidate["preferredPoolId"]
        for candidate in overview_path["hopCandidates"]
    }
    assert preferred_by_leg == {"x-y": deep_ids[0], "y-z": deep_ids[2], "z-x": deep_ids[1]}

    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=46630, protocols=["uniswap_v3"])
    quote_route = next(route for route in quotes["routes"] if route.get("symbolPair") == target_path)
    assert quote_route["hopCandidateCombinationCount"] == 8
    assert quote_route["recommendedFirstPriority"] is True
    assert quote_route["routeRecommendationBasis"] == "tvl_depth_desc"
    assert quote_route["hopCandidateSelection"]["selectionBasis"] == "tvl_depth_desc"
    assert quote_route["hopCandidateSelection"]["preferredPoolIds"] == [
        deep_ids[0],
        deep_ids[2],
        deep_ids[1],
    ]


def test_dex_best_pool_selector_extension_point_controls_preferred_pool():
    from market.binance_market.dex_surface_observation_sequences import (
        _hop_candidate_set,
        _select_best_pool_rows,
    )
    from market.binance_market.dex_surface_types import _pool_address

    pool_deep = _pool("0x" + "a" * 40, "USDC", "WETH", "0x" + "1" * 40, "0x" + "2" * 40, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=2_000_000)
    pool_shallow = _pool("0x" + "b" * 40, "USDC", "WETH", "0x" + "1" * 40, "0x" + "2" * 40, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=1_000_000)
    for pool in (pool_deep, pool_shallow):
        pool["hasLiquidity"] = True
        pool["usableForObservation"] = True

    assert _pool_address(_select_best_pool_rows([pool_deep, pool_shallow])[0]) == pool_deep["pool"]

    selector_rows = _select_best_pool_rows(
        [pool_deep, pool_shallow],
        selector=lambda rows: list(reversed(rows)),
    )
    candidate = _hop_candidate_set(
        leg="x-y",
        direction="yc",
        from_token={"symbol": "USDC", "address": "0x" + "1" * 40},
        to_token={"symbol": "WETH", "address": "0x" + "2" * 40},
        pools=selector_rows,
    )
    assert candidate["preferredPoolId"] == pool_shallow["pool"]


