import pytest
from flask import Flask

from market.binance_market import quote_service
from market.binance_market import pool_candidates
from market.binance_market import dex_surfaces
from market.binance_market import pool_candidate_filtering
from market.binance_market.pool_candidate_filtering import _candidate_pool_source_available
from market.binance_market.candidate_pool_cache import store as candidate_pool_cache_store
from dex.intent_trade.chains.common.dex_candidate_tables import build_anchor_candidate_groups
from dex.intent_trade.chains.robinhood_testnet import market_observation as robinhood_testnet_market_observation
from core.chain_data_registry import chain_data_by_chain_id
from market.strategy.arbitrage import ArbitrageConfig
from market.binance_market.dex_surface_v4 import _static_depth_available
from web.data_routes import dex_surface_response


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


def test_robinhood_testnet_candidate_pagination_defaults_to_20_rows(monkeypatch):
    app = Flask(__name__)
    with app.test_request_context("/api/dex-pools/candidates?chain_id=46630"):
        page, page_size, query, quality = dex_surface_response.candidate_pagination_args()
        assert page == 1
        assert page_size == 20
        assert query == ""
        assert quality == "all"


def test_non_testnet_candidate_pagination_keeps_legacy_default(monkeypatch):
    app = Flask(__name__)
    with app.test_request_context("/api/dex-pools/candidates?chain_id=4663"):
        _, page_size, _, _ = dex_surface_response.candidate_pagination_args()
        assert page_size == 20


def test_candidate_pagination_allows_larger_page_size_when_requested(monkeypatch):
    app = Flask(__name__)
    with app.test_request_context("/api/dex-pools/candidates?chain_id=46630&page_size=1000"):
        _, page_size, _, _ = dex_surface_response.candidate_pagination_args()
        assert page_size == 1000


def test_robinhood_mainnet_static_quote_routes_keep_protocol_scope():
    assert dex_surfaces._robinhood_static_scope_protocols(
        chain_id=4663,
        protocols=["uniswap_v3", "uniswap_v4"],
        use_static_observation=True,
    ) == ["uniswap_v3", "uniswap_v4"]
    assert dex_surfaces._robinhood_static_scope_protocols(
        chain_id=4663,
        protocols=["uniswap_v3"],
        use_static_observation=True,
    ) == ["uniswap_v3"]


def test_robinhood_testnet_static_quote_routes_keep_protocol_scope():
    assert dex_surfaces._robinhood_static_scope_protocols(
        chain_id=46630,
        protocols=["uniswap_v3", "uniswap_v4"],
        use_static_observation=True,
    ) == ["uniswap_v3", "uniswap_v4"]


def test_build_binance_market_state_uses_shared_pool_candidates(monkeypatch):
    monkeypatch.setattr(
        quote_service,
        "dex_pool_candidates_payload",
        lambda: {
            "mode": "stub_pool_candidates",
            "pairs": [],
            "summary": {},
            "poolStaticTable": {},
        },
    )

    payload = quote_service.build_binance_market_state(
        {},
        aave_symbols=[],
        arbitrage_config=ArbitrageConfig(notional_usd=1, trade_fee_percent=0, flashloan_fee_percent=0),
        top_limit=1,
        bottom_limit=1,
    )

    assert payload["pool_candidates"]["mode"] == "stub_pool_candidates"


def test_robinhood_mainnet_candidate_source_available_with_relative_data_file(monkeypatch, tmp_path):
    fake_path = tmp_path / "robinhood_dex_candidates.json"
    fake_path.write_text("{}", encoding="utf-8")

    def fake_chain_data_file_path(network, key, *, chain_id=None, root=None):
        assert key == "dex_candidates"
        assert chain_id == 4663
        return fake_path

    monkeypatch.setattr(pool_candidate_filtering, "chain_data_file_path", fake_chain_data_file_path)

    assert _candidate_pool_source_available(4663) is True


def test_robinhood_v4_pool_manager_address_is_same_on_mainnet_and_testnet():
    mainnet = chain_data_by_chain_id(4663) or {}
    testnet = chain_data_by_chain_id(46630) or {}
    mainnet_contracts = mainnet.get("contracts") or {}
    testnet_contracts = testnet.get("contracts") or {}

    for key in ("v4_pool_manager", "v4_quoter", "position_manager", "permit2", "universal_router"):
        assert str(mainnet_contracts.get(key) or "").lower()
        assert str(mainnet_contracts.get(key) or "").lower() == str(testnet_contracts.get(key) or "").lower(), key
    assert str(mainnet_contracts.get("v4_pool_manager") or "").lower() == "0x8366a39cc670b4001a1121b8f6a443a643e40951"


def test_anchor_candidate_groups_split_yc_and_zc_by_anchor():
    pair_reports = [
        {
            "symbolPair": "USDT:ETH",
            "tokenXSymbol": "USDT",
            "tokenYSymbol": "ETH",
            "classification": {"stableSymbols": ["USDT"], "nonStableSymbols": ["ETH"]},
            "preferredPools": [
                {
                    "pool": "0x" + "1" * 40,
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "fee": "0",
                    "quality": "pass",
                    "token0": "0x" + "a" * 40,
                    "token1": "0x" + "b" * 40,
                    "token0Symbol": "USDT",
                    "token1Symbol": "ETH",
                    "balances": {"tvlUsd": 1000},
                    "depthBand": {"level": "deep", "label": "deep"},
                }
            ],
            "strictQualityPools": [],
            "executionPools": [],
        }
    ]

    groups = build_anchor_candidate_groups(pair_reports)

    assert groups[0]["anchorSymbol"] == "USDT"
    assert groups[0]["ycPoolCount"] == "1"
    assert groups[0]["zcPoolCount"] == "1"
    assert groups[0]["ycTokens"][0]["symbol"] == "ETH"
    assert groups[0]["zcTokens"][0]["symbol"] == "ETH"
    assert groups[0]["ycPools"][0]["routeText"] == "USDT -> ETH"
    assert groups[0]["zcPools"][0]["routeText"] == "ETH -> USDT"


def test_anchor_candidate_groups_include_all_symbols_when_mixed_pairs_are_present():
    pair_reports = [
        {
            "symbolPair": "USDC:FRAX",
            "tokenXSymbol": "USDC",
            "tokenYSymbol": "FRAX",
            "classification": {"stableSymbols": ["USDC", "FRAX"], "nonStableSymbols": []},
            "preferredPools": [
                {
                    "pool": "0x" + "1" * 40,
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "fee": "0",
                    "quality": "pass",
                    "token0": "0x" + "a" * 40,
                    "token1": "0x" + "b" * 40,
                    "token0Symbol": "USDC",
                    "token1Symbol": "FRAX",
                    "balances": {"tvlUsd": 1000},
                    "depthBand": {"level": "deep", "label": "deep"},
                }
            ],
            "strictQualityPools": [],
            "executionPools": [],
        },
        {
            "symbolPair": "WETH:WAVAX",
            "tokenXSymbol": "WETH",
            "tokenYSymbol": "WAVAX",
            "classification": {"stableSymbols": [], "nonStableSymbols": ["WETH", "WAVAX"]},
            "preferredPools": [
                {
                    "pool": "0x" + "2" * 40,
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "fee": "0",
                    "quality": "pass",
                    "token0": "0x" + "c" * 40,
                    "token1": "0x" + "d" * 40,
                    "token0Symbol": "WETH",
                    "token1Symbol": "WAVAX",
                    "balances": {"tvlUsd": 2000},
                    "depthBand": {"level": "deep", "label": "deep"},
                }
            ],
            "strictQualityPools": [],
            "executionPools": [],
        },
    ]

    groups = build_anchor_candidate_groups(pair_reports)
    anchor_symbols = [group["anchorSymbol"] for group in groups]

    assert anchor_symbols == ["FRAX", "USDC", "WAVAX", "WETH"]
    assert {group["anchorSymbol"] for group in groups} == {"FRAX", "USDC", "WAVAX", "WETH"}


def test_anchor_candidate_groups_use_all_pools_and_keep_unique_queues():
    pool_a = {
        "pool": "0x" + "1" * 40,
        "protocol": "uniswap_v3",
        "dex": "uniswap_v3",
        "fee": "3000",
        "quality": "pass",
        "token0": "0x" + "a" * 40,
        "token1": "0x" + "b" * 40,
        "token0Symbol": "USDC",
        "token1Symbol": "ETH",
        "balances": {"tvlUsd": 1000},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    pool_b = {
        "pool": "0x" + "2" * 40,
        "protocol": "uniswap_v3",
        "dex": "uniswap_v3",
        "fee": "3000",
        "quality": "rejected",
        "token0": "0x" + "a" * 40,
        "token1": "0x" + "b" * 40,
        "token0Symbol": "USDC",
        "token1Symbol": "ETH",
        "balances": {"tvlUsd": 500},
        "depthBand": {"level": "shallow", "label": "shallow"},
    }

    pair_reports = [
        {
            "symbolPair": "USDC:ETH",
            "tokenXSymbol": "USDC",
            "tokenYSymbol": "ETH",
            "classification": {"stableSymbols": ["USDC"], "nonStableSymbols": ["ETH"]},
            "allPools": [pool_a, pool_a, pool_b],
            "preferredPools": [pool_a],
            "strictQualityPools": [pool_a],
            "executionPools": [pool_a],
            "rejectedPools": [pool_b],
        }
    ]

    groups = build_anchor_candidate_groups(pair_reports)
    group = groups[0]

    assert group["candidatePoolCount"] == "2"
    assert group["candidateQueueCount"] == "4"
    assert len(group["ycPools"]) == 2
    assert len(group["zcPools"]) == 2
    assert len({row["routeKey"] for row in group["ycPools"]}) == 2


def test_robinhood_testnet_v4_pool_candidates_use_chain_owned_flow():
    payload = pool_candidates.dex_pool_candidates_payload(chain_id=46630, protocols=["uniswap_v4"])

    assert payload["poolStaticTable"]["network"] == "robinhood-testnet"
    assert payload["poolStaticTable"]["chainId"] == 46630
    assert payload["poolStaticTable"]["mode"] in {
        "robinhood_testnet_v4_pool_index",
        "robinhood_testnet_v4_pool_liquidity",
        "robinhood_testnet_v4_pool_index_active",
        "robinhood_testnet_v4_pool_liquidity_fallback",
        "robinhood_testnet_v4_pool_sources_merged",
    }
    assert int(payload["summary"].get("candidateRouteCount") or 0) > 0
    assert payload["poolStaticTable"]["exists"] is True
    assert payload["processing"]["handler"] == (
        "intent_trade.chains.robinhood_testnet.dex_pool_candidates:dex_pool_candidates_payload"
    )
    assert int(payload["summary"]["pairTableCount"]) > 0
    assert int(payload["summary"]["anchorGroupCount"]) > 0
    assert int(payload["summary"]["anchorGroupCount"]) <= int(payload["summary"]["flashAccountingAssetCount"])
    assert payload["summary"]["candidateSetStorageMode"] == "shared_yc_zc_pool_set"
    assert int(payload["summary"]["ycZcSameSetGroupCount"]) > 0
    assert 0 < int(payload["summary"]["candidateRouteCount"]) <= int(payload["summary"]["v4IndexedPoolCount"])
    assert payload["pairs"][0]["identityMode"] == "pool_key"
    assert payload["pairs"][0]["preferredPools"][0]["poolIdentityMode"] == "pool_id"
    assert payload["pairs"][0]["preferredPools"][0]["protocol"] == "uniswap_v4"
    assert payload["anchorGroups"]
    assert payload["anchorGroups"][0]["ycZcSameSet"] is True
    assert payload["anchorGroups"][0]["ycPoolsRef"] == "sharedPools"
    assert payload["anchorGroups"][0]["sharedPools"]


def test_robinhood_testnet_v4_pool_candidates_merge_disjoint_index_and_liquidity_sources():
    from dex.intent_trade.chains.robinhood_testnet.dex_pool_candidates import _candidate_source_pools

    indexed = {"ok": True, "source": "index.json", "pools": [{"poolId": "0x01"}]}
    liquidity = {
        "ok": True,
        "source": "liquidity.json",
        "pools": [{"poolId": "0x02"}],
    }

    pools, mode, source = _candidate_source_pools(indexed, liquidity)

    assert [pool["poolId"] for pool in pools] == ["0x01", "0x02"]
    assert mode == "robinhood_testnet_v4_pool_sources_merged"
    assert source == "index.json"


def test_robinhood_testnet_v4_pool_candidates_include_known_cold_pool_ids(monkeypatch):
    from dex.intent_trade.chains.robinhood_testnet import dex_pool_candidates

    active_id = "0x" + "c" * 64
    known_id = "0x" + "a" * 64
    monkeypatch.setattr(
        dex_pool_candidates,
        "_known_token_pool_ids",
        lambda: {"0xknown": [f"testnet-v4-amm-manifest.tz", known_id]},
    )
    indexed = {
        "ok": True,
        "source": "index.json",
        "pools": [
            {"poolId": active_id, "token0": "0xactive0", "token1": "0xactive1"},
            {"poolId": known_id, "token0": "0xtz", "token1": "0xta"},
        ],
    }
    liquidity = {
        "ok": True,
        "source": "liquidity.json",
        "pools": [{"poolId": active_id, "token0": "0xactive0", "token1": "0xactive1"}],
    }

    pools, mode, _ = dex_pool_candidates._candidate_source_pools(indexed, liquidity)

    assert {pool["poolId"] for pool in pools} == {active_id, known_id}
    assert mode == "robinhood_testnet_v4_pool_sources_merged"


def test_v4_static_depth_available_for_indexed_sqrt_price_pool():
    assert _static_depth_available(
        {
            "protocol": "uniswap_v4",
            "liquidity": "",
            "tvlUsd": 0,
            "sqrtPriceX96": "25054144837438405210904448839",
        }
    ) is True
    assert _static_depth_available(
        {
            "protocol": "uniswap_v4",
            "liquidity": "",
            "tvlUsd": 0,
            "sqrtPriceX96": "",
        }
    ) is False


def test_dex_pool_candidates_classifies_stable_and_non_stable_pools(tmp_path, monkeypatch):
    cache_path = tmp_path / "bnb_arbitrage_pool_static_table.json"
    _write_json(
        cache_path,
        {
            "network": "bnb",
            "chainId": 56,
            "pools": [
                {
                    "pool": "0x" + "1" * 40,
                    "symbolPair": "USDC:USDT",
                    "token0Symbol": "USDC",
                    "token1Symbol": "USDT",
                    "token0Usd": 500_000,
                    "token1Usd": 500_000,
                    "tvlUsd": 1_000_000,
                    "status": "recommended",
                },
                {
                    "pool": "0x" + "2" * 40,
                    "symbolPair": "USDC:WBNB",
                    "token0Symbol": "USDC",
                    "token1Symbol": "WBNB",
                    "token0Usd": 250_000,
                    "token1Usd": 260_000,
                    "tvlUsd": 510_000,
                    "status": "recommended",
                },
                {
                    "pool": "0x" + "3" * 40,
                    "symbolPair": "BTCB:WBNB",
                    "token0Symbol": "BTCB",
                    "token1Symbol": "WBNB",
                    "token0Usd": 100_000,
                    "token1Usd": 100_000,
                    "tvlUsd": 200_000,
                    "status": "recommended",
                },
            ],
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {
            "protocols": [{"protocol": "venus_core"}, {"protocol": "pancakeswap_v2"}],
            "removed_protocols": [],
        },
    )

    payload = pool_candidates.dex_pool_candidates_payload(cache_path)

    assert payload["summary"]["pairClassCounts"] == {
        "non_stable_pair": 1,
        "stable_pair": 1,
        "stable_to_non_stable_pair": 1,
    }
    assert payload["summary"]["stablePairCount"] == "1"
    assert payload["flashLoanLiquidity"]["available_protocols"] == ["venus_core", "pancakeswap_v2"]
    assert payload["dexSettlement"]["uniswapx"]["supported"] is True
    assert payload["dexSettlement"]["arbitrage_integration"]["can_combine_with_flashloan"] is True
    by_pair = {item["symbolPair"]: item for item in payload["pairs"]}
    assert by_pair["USDC:USDT"]["classification"]["pairClass"] == "stable_pair"
    assert by_pair["USDC:WBNB"]["classification"]["pairClass"] == "stable_to_non_stable_pair"
    assert by_pair["BTCB:WBNB"]["classification"]["pairClass"] == "non_stable_pair"
    assert by_pair["USDC:WBNB"]["flashLoanLinkage"]["direct_stable_flashloan"] is True
    assert by_pair["USDC:WBNB"]["flashLoanLinkage"]["ready_for_atomic_arbitrage"] is True


def test_dex_pool_candidates_marks_uniswapx_as_settlement_not_pool(tmp_path, monkeypatch):
    cache_path = tmp_path / "polygon_arbitrage_pool_static_table.json"
    _write_json(
        cache_path,
        {
            "network": "polygon",
            "chainId": 137,
            "pairs": [
                {
                    "symbolPair": "USDC:WETH",
                    "tokenXSymbol": "USDC",
                    "tokenYSymbol": "WETH",
                    "allPools": [
                        {
                            "pool": "0x" + "4" * 40,
                            "token0_symbol": "USDC",
                            "token1_symbol": "WETH",
                            "tvlUsd": 100_000,
                            "quality": "pass",
                        }
                    ],
                    "preferredPools": [
                        {
                            "pool": "0x" + "4" * 40,
                            "token0_symbol": "USDC",
                            "token1_symbol": "WETH",
                            "tvlUsd": 100_000,
                            "quality": "pass",
                        }
                    ],
                    "executionPools": [],
                }
            ],
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": [{"protocol": "aave_v3"}]},
    )

    payload = pool_candidates.dex_pool_candidates_payload(cache_path)

    assert payload["dexSettlement"]["uniswapx"]["pool_model"] == "intent_order_flow_not_amm_pool"
    assert payload["dexSettlement"]["uniswapx"]["supported"] is False
    assert payload["dexSettlement"]["arbitrage_integration"]["can_combine_with_flashloan"] is False
    assert payload["pairs"][0]["settlementRails"] == []
    assert payload["pairs"][0]["flashLoanLinkage"]["ready_for_atomic_arbitrage"] is False


def test_dex_quote_routes_are_driven_by_candidate_pools_not_flashloan(tmp_path, monkeypatch):
    cache_path = tmp_path / "bnb_arbitrage_pool_static_table.json"
    _write_json(
        cache_path,
        {
            "network": "bnb",
            "chainId": 56,
            "pools": [
                {
                    "pool": "0x" + "5" * 40,
                    "symbolPair": "USDC:WBNB",
                    "dex": "pancakeswap_v2",
                    "protocol": "v2",
                    "token0Symbol": "USDC",
                    "token1Symbol": "WBNB",
                    "token0Usd": 600_000,
                    "token1Usd": 610_000,
                    "tvlUsd": 1_210_000,
                    "status": "recommended",
                }
            ],
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )
    monkeypatch.setattr(pool_candidates, "_candidate_pool_source_available", lambda chain_id: True)
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: pool_candidates.dex_pool_candidates_payload(cache_path))

    candidates = pool_candidates.dex_pool_candidates_payload(cache_path)
    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=56)
    request = dex_surfaces.dex_quote_request_payload(chain_id=56, pair="USDC -> WBNB -> USDC", venue="uniswapx", amount="1000")

    assert candidates["pairs"][0]["preferredPoolCount"] == "1"
    assert candidates["pairs"][0]["flashLoanLinkage"]["ready_for_atomic_arbitrage"] is False
    assert quotes["summary"]["routeCount"] == 1
    assert quotes["summary"]["probeNotionalUsd"] == 1000.0
    assert quotes["summary"]["completeStaticQuoteCount"] == 1
    assert quotes["summary"]["uniswapxQuoteableRouteCount"] == 1
    assert quotes["routes"][0]["overviewRoute"]["anchorBorrowSymbol"] == "USDC"
    assert quotes["routes"][0]["overviewRoute"]["targetSymbol"] == "WBNB"
    assert quotes["routes"][0]["quoteAnalysis"]["notionalUsd"] == 1000.0
    assert quotes["routes"][0]["quoteAnalysis"]["stepCount"] == 2
    assert quotes["routes"][0]["quoteAnalysis"]["steps"][0]["fromSymbol"] == "USDC"
    assert quotes["routes"][0]["quoteAnalysis"]["steps"][0]["toSymbol"] == "WBNB"
    analysis = quotes["routes"][0]["quoteAnalysis"]
    size_rows = quotes["routes"][0].get("sizeAnalysis", {}).get("rows") or []
    assert (
        analysis.get("netProfitUsd") is not None
        or analysis.get("referenceNetProfitUsd") is not None
        or any(row.get("observedNetProfitUsd") is not None for row in size_rows)
    )
    assert quotes["routes"][0]["quoteAnalysis"]["recommendation"]["action"]
    assert quotes["routes"][0]["quoteInputs"]["quoteRequests"][0]["venue"] == "amm_pool"
    assert quotes["routes"][0]["quoteInputs"]["routePools"]
    assert len(quotes["routes"][0]["quoteInputs"]["routePools"]) >= len(quotes["routes"][0]["quoteInputs"]["candidatePools"])
    assert any(item["venue"] == "uniswapx" for item in quotes["routes"][0]["quoteInputs"]["quoteRequests"])
    assert request["ok"] is True
    assert request["status"] == "quote_request_ready"
    assert request["quoteAnalysis"]["notionalUsd"] == 1000.0
    assert request["quoteRequests"][0]["venue"] == "uniswapx"


def test_dex_quote_routes_keep_uniswap_v4_quote_requests(tmp_path, monkeypatch):
    cache_path = tmp_path / "bnb_arbitrage_pool_static_table.json"
    _write_json(
        cache_path,
        {
            "network": "bnb",
            "chainId": 56,
            "pools": [
                {
                    "pool": "0x" + "9" * 40,
                    "symbolPair": "USDC:WBNB",
                    "dex": "uniswap_v4",
                    "protocol": "uniswap_v4",
                    "token0Symbol": "USDC",
                    "token1Symbol": "WBNB",
                    "token0Usd": 600_000,
                    "token1Usd": 610_000,
                    "tvlUsd": 1_210_000,
                    "status": "recommended",
                }
            ],
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: pool_candidates.dex_pool_candidates_payload(cache_path))

    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=56)
    request = dex_surfaces.dex_quote_request_payload(chain_id=56, pair="USDC -> WBNB -> USDC", venue="uniswap_v4", amount="1000")

    assert quotes["summary"]["liveQuoterRequiredCount"] == 1
    assert any(item["venue"] == "uniswap_v4" for item in quotes["routes"][0]["quoteInputs"]["quoteRequests"])
    v4_request = next(item for item in request["quoteRequests"] if item["venue"] == "uniswap_v4")
    assert request["ok"] is True
    assert request["status"] == "quote_request_ready"
    assert v4_request["quoteMode"] == "uniswap_v4_sdk_exact_input_route"
    assert v4_request["status"] == "sdk_adapter_required"


def test_dex_opportunity_scanner_records_uniswap_v4_adapter_required_routes(tmp_path):
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    recorded_batches = []

    def quote_payload_provider(*, chain_id=None, protocols=None):
        return {
            "poolStaticTable": {"network": "robinhood", "chainId": 4663},
            "summary": {
                "routeCount": 1,
                "completeStaticQuoteCount": 1,
                "executableCandidateCount": 0,
                "profitableRouteCount": 0,
                "liveQuoterRequiredCount": 1,
                "sdkAdapterRequiredCount": 1,
                "bestObservedNetProfitUsd": None,
            },
            "routes": [
                {
                    "symbolPair": "USDC -> WBNB -> USDC",
                    "routeSymbols": ["USDC", "WBNB", "USDC"],
                    "quoteStatus": "requires_live_quoter",
                    "overviewRoute": {"kind": "direct_anchor_pair", "targetSymbol": "WBNB"},
                    "quoteInputs": {
                        "candidatePools": [
                            {
                                "pool": "0x" + "9" * 40,
                                "dex": "uniswap_v4",
                                "protocol": "uniswap_v4",
                                "token0Symbol": "USDC",
                                "token1Symbol": "WBNB",
                            }
                        ],
                        "quoteRequests": [
                            {
                                "venue": "uniswap_v4",
                                "quoteMode": "uniswap_v4_sdk_exact_input_single",
                                "status": "sdk_adapter_required",
                            }
                        ],
                    },
                    "quoteAnalysis": {
                        "method": "static_amm_depth_probe",
                        "notionalUsd": 1000.0,
                        "inputSymbol": "USDC",
                        "outputSymbol": "USDC",
                        "finalOutputUsd": None,
                        "netProfitUsd": None,
                        "profitable": False,
                        "complete": True,
                        "requiresLiveQuoter": True,
                        "adapterRequired": True,
                        "executableCandidate": False,
                        "recommendation": {
                            "level": "requires_live_quote",
                            "action": "等待链上 Quoter 复核",
                            "reasons": ["v4 SDK adapter required"],
                        },
                        "steps": [
                            {
                                "hop": 1,
                                "fromSymbol": "USDC",
                                "toSymbol": "WBNB",
                                "executionTrust": "sdk_adapter_required",
                                "quoteSource": "uniswap_v4_sdk_adapter_required",
                            }
                        ],
                    },
                }
            ],
        }

    def record_attempts(attempts):
        recorded_batches.append(attempts)
        return {"recorded": len(attempts), "source": "jsonl", "error": None}

    def onchain_evidence_provider(route, **kwargs):
        return {
            "evidenceVersion": 1,
            "observedAt": kwargs.get("observed_at"),
            "network": "robinhood",
            "chainId": 4663,
            "status": "live_quote_failed",
            "executionTrust": "static_reference_only",
            "trustedForExecution": False,
            "blockNumber": None,
            "triggerContext": kwargs.get("trigger_context") or {},
            "triggerBlockNumber": (kwargs.get("trigger_context") or {}).get("triggerBlockNumber"),
            "quoteBlockSource": "trigger_block",
            "routeSymbols": route.get("routeSymbols") or [],
            "hops": [],
            "costModel": {},
            "blockers": ["uniswap v4 quote adapter is not configured"],
            "transactionDevelopment": {"status": "blocked_before_transaction_development", "executionEnabled": False, "blockers": ["uniswap v4 quote adapter is not configured"]},
        }

    scanner = DexOpportunityScanner(
        interval_seconds=3,
        record_cooldown_seconds=0,
        quote_payload_provider=quote_payload_provider,
        record_attempts=record_attempts,
        onchain_evidence_provider=onchain_evidence_provider,
        trigger_context_provider=lambda **kwargs: {
            "source": "test_trigger",
            "observedAt": kwargs.get("observed_at"),
            "network": "robinhood",
            "chainId": 4663,
            "available": True,
            "triggerBlockNumber": 12345,
            "blockNumber": 12345,
        },
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
    )

    payload = scanner.scan_now(chain_id=4663, protocols=["uniswap_v4"])

    assert payload["snapshot"]["summary"]["sdk_adapter_required_count"] == 0
    assert payload["snapshot"]["summary"]["static_sdk_adapter_required_count"] == 1
    assert payload["snapshot"]["recording"]["candidate_count"] == 1
    assert payload["snapshot"]["recording"]["recorded"] == 1
    assert len(recorded_batches) == 1
    assert recorded_batches[0][0]["quote"]["live_onchain_evidence"]["status"] == "live_quote_failed"
    assert recorded_batches[0][0]["quote"]["quote_requests"][0]["status"] == "sdk_adapter_required"


