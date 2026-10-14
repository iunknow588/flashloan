import pytest

from market.binance_market import quote_service
from market.binance_market import pool_candidates
from market.binance_market import dex_surfaces
from market.binance_market.dex_surface_observation_impl import _path_candidates_from_market_observation
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


def _obs_route(symbols, addresses):
    return {
        "kind": "bridge_closed_loop",
        "anchorSymbol": symbols[0],
        "targetSymbol": symbols[1],
        "closedLoop": True,
        "pathSymbols": symbols,
        "pathText": " -> ".join(symbols),
        "routeTokenPath": [
            {"symbol": symbol, "address": address}
            for symbol, address in zip(symbols, addresses)
        ],
        "routeAddresses": addresses,
        "routePools": [
            {
                "pool": "0x" + str(index + 1) * 40,
                "protocol": "uniswap_v4",
                "fee": 3000,
                "routeSymbols": [symbols[index], symbols[index + 1]],
                "routeAddresses": [addresses[index], addresses[index + 1]],
            }
            for index in range(len(symbols) - 1)
        ],
        "routePairs": [],
    }


def test_path_candidates_dedupe_reverse_closed_loop_directions():
    usdc = "0x" + "1" * 40
    weth = "0x" + "2" * 40
    dai = "0x" + "3" * 40
    observation = {
        "closedLoopRoutes": [
            _obs_route(["USDC", "WETH", "DAI", "USDC"], [usdc, weth, dai, usdc]),
            _obs_route(["USDC", "DAI", "WETH", "USDC"], [usdc, dai, weth, usdc]),
        ]
    }
    candidates = _path_candidates_from_market_observation(observation, chain_id=46630)
    assert len(candidates) == 1
    selected_symbols = candidates[0]["overviewRoute"]["pathSymbols"]
    assert selected_symbols[0] == "USDC"
    assert set(selected_symbols) == {"USDC", "WETH", "DAI"}


def test_clear_cow_execution_attempts_jsonl_filters_by_network(tmp_path):
    from core.db.storage_cow_execution import (
        append_cow_execution_attempts_jsonl,
        clear_cow_execution_attempts_jsonl,
        load_recent_cow_execution_attempts_jsonl,
    )

    path = tmp_path / "cow_execution_attempts.jsonl"
    attempts = [
        {
            "observed_at": "2026-08-24T00:00:00+00:00",
            "network": "robinhood",
            "pair": "USDG -> WETH -> USDG",
            "state": "quote_required",
            "execution_phase": "market_candidate",
            "checks_passed": False,
            "can_submit_order": False,
        },
        {
            "observed_at": "2026-08-24T00:00:01+00:00",
            "network": "bnb",
            "pair": "USDC -> WBNB -> USDC",
            "state": "quote_required",
            "execution_phase": "market_candidate",
            "checks_passed": False,
            "can_submit_order": False,
        },
    ]

    assert append_cow_execution_attempts_jsonl(path, attempts) == 2
    result = clear_cow_execution_attempts_jsonl(path, networks=["robinhood"])
    rows = load_recent_cow_execution_attempts_jsonl(path, limit=10)

    assert result["cleared"] == 1
    assert [row["network"] for row in rows] == ["bnb"]


def test_dex_overview_does_not_reuse_robinhood_borrow_anchor_on_other_chains(tmp_path, monkeypatch):
    cache_path = tmp_path / "bnb_arbitrage_pool_static_table.json"
    _write_json(
        cache_path,
        {
            "network": "bnb",
            "chainId": 56,
            "pools": [
                {
                    "pool": "0x" + "6" * 40,
                    "symbolPair": "USDC:WBNB",
                    "dex": "pancakeswap_v2",
                    "protocol": "v2",
                    "token0Symbol": "USDC",
                    "token1Symbol": "WBNB",
                    "token0Usd": 400_000,
                    "token1Usd": 390_000,
                    "tvlUsd": 790_000,
                    "status": "recommended",
                }
            ],
        },
    )
    monkeypatch.setattr(
        dex_surfaces,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {
            "protocols": [],
            "removed_protocols": [],
            "raw_snapshots": {
                "robinhood_flash_liquidity": {
                    "chainId": 4663,
                    "sources": [
                        {"protocol": "morpho_blue", "token_symbol": "USDG", "flash_borrowable": True},
                    ],
                }
            },
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )
    monkeypatch.setattr(
        dex_surfaces,
        "dex_pool_candidates_payload",
        lambda chain_id=None, protocols=None: pool_candidates.dex_pool_candidates_payload(cache_path),
    )

    overview = dex_surfaces.dex_overview_payload(chain_id=56)

    assert overview["summary"]["anchorBorrowSymbols"] == ["USDC"]
    assert overview["arbitragePaths"] == []


def test_robinhood_testnet_dex_surfaces_use_all_candidate_symbols(monkeypatch):
    pool_template = lambda suffix, left, right: {
        "pool": f"0x{suffix * 40}",
        "protocol": "uniswap_v4",
        "dex": "uniswap_v4",
        "fee": "0",
        "quality": "pass",
        "token0": f"0x{left.lower() * 40}",
        "token1": f"0x{right.lower() * 40}",
        "token0Symbol": left,
        "token1Symbol": right,
        "balances": {"tvlUsd": 1000},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    payload = {
        "pairs": [
            {
                "symbolPair": "FRAX:USDC",
                "tokenXSymbol": "FRAX",
                "tokenYSymbol": "USDC",
                "tokenX": "0x" + "a" * 40,
                "tokenY": "0x" + "b" * 40,
                "preferredPools": [pool_template("1", "FRAX", "USDC")],
                "executionPools": [pool_template("1", "FRAX", "USDC")],
                "strictQualityPools": [pool_template("1", "FRAX", "USDC")],
            },
            {
                "symbolPair": "ETH:CCOX",
                "tokenXSymbol": "ETH",
                "tokenYSymbol": "CCOX",
                "tokenX": "0x" + "c" * 40,
                "tokenY": "0x" + "d" * 40,
                "preferredPools": [pool_template("2", "ETH", "CCOX")],
                "executionPools": [pool_template("2", "ETH", "CCOX")],
                "strictQualityPools": [pool_template("2", "ETH", "CCOX")],
            },
        ],
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
    }

    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)
    monkeypatch.setattr(
        dex_surfaces,
        "robinhood_testnet_v4_bridge_pairs_for_anchor_groups",
        lambda anchor_groups, chain_id=None, protocols=None: ([], {"enabled": True, "status": "test_disabled"}),
    )
    monkeypatch.setattr(
        dex_surfaces,
        "robinhood_testnet_v4_fund_anchor_pair_observation",
        lambda chain_id=None, protocols=None: {"enabled": True, "status": "test_disabled", "observationSequences": []},
    )
    monkeypatch.setattr(
        dex_surfaces,
        "_route_quote_analysis",
        lambda route, price_map=None, notional_usd=None, **kwargs: {
            "method": "mock",
            "notionalUsd": notional_usd or 1000.0,
            "inputSymbol": (route.get("routeSymbols") or [None])[0],
            "outputSymbol": (route.get("routeSymbols") or [None])[-1],
            "complete": True,
            "requiresLiveQuoter": False,
            "adapterRequired": False,
            "stepCount": 0,
            "steps": [],
            "finalOutputUsd": 1010.0,
            "referenceFinalOutputUsd": 1010.0,
            "grossProfitUsd": 10.0,
            "referenceGrossProfitUsd": 10.0,
            "flashLoanFeeUsd": 0.5,
            "gasReserveUsd": 1.0,
            "minProfitUsd": 1.0,
            "offchainMinProfitUsd": 1.0,
            "submissionMinProfitUsd": 0.5,
            "submissionNetProfitUsd": 9.5,
            "offchainNetProfitUsd": 8.5,
            "netProfitUsd": 8.5,
            "referenceNetProfitUsd": 8.5,
            "netProfitBps": 85.0,
            "referenceNetProfitBps": 85.0,
            "profitable": True,
            "executableCandidate": True,
            "sdkAdapterRequiredStepCount": 0,
            "recommendation": {"level": "ready_for_precheck", "action": "进入自动交易预检", "reasons": []},
            "warnings": [],
        },
    )

    expected_symbols = ["AMD", "AMZN", "ETH", "NFLX", "PLTR", "TSLA", "TZ", "USDE", "USDG", "WETH"]
    db_observation = {
        "mode": "dex_market_observation",
        "chain_id": 46630,
        "selected_protocols": ["uniswap_v4"],
        "marketObservationSource": "sqlite_db",
        "marketObservationSourceStage": "sqlite_db",
        "summary": {
            "observationAnchorCount": str(len(expected_symbols)),
            "observationSequenceCount": 0,
        },
        "observationAnchorSymbols": expected_symbols,
        "anchorGroups": [],
        "closedLoopRoutes": [],
        "pairs": [],
        "bridgePairs": [],
    }
    monkeypatch.setattr(
        dex_surfaces,
        "_database_first_robinhood_observation_payload",
        lambda **kwargs: db_observation,
    )
    monkeypatch.setattr(
        dex_surfaces,
        "_observation_anchor_symbols",
        lambda pool_payload, **kwargs: expected_symbols,
    )
    monkeypatch.setattr(
        dex_surfaces,
        "_route_framework_context_from_observation",
        lambda observation, pool_payload, **kwargs: {
            "overview_paths": [],
            "watch_pairs": [],
            "anchor_symbols": expected_symbols,
            "closed_loop_anchor_symbols": expected_symbols,
            "observation_anchor_symbols": expected_symbols,
            "path_source": "market_observation",
            "used_market_observation": True,
        },
    )

    overview = dex_surfaces.dex_overview_payload(chain_id=46630, protocols=["uniswap_v4"])
    observation = dex_surfaces.dex_market_observation_payload(chain_id=46630, protocols=["uniswap_v4"])
    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=46630, protocols=["uniswap_v4"])

    assert overview["decisionBasis"] in {
        "robinhood_testnet_market_observation_closed_loop",
        "symbol_anchor_to_non_anchor_candidate_path",
    }
    assert overview["summary"]["anchorBorrowSymbols"] == expected_symbols
    assert observation["summary"]["observationAnchorCount"] == len(expected_symbols)
    assert observation["observationAnchorSymbols"] == expected_symbols
    assert quotes["summary"]["anchorBorrowSymbols"] == expected_symbols
    assert quotes["summary"]["closedLoopAnchorSymbols"] == expected_symbols
    assert quotes["summary"]["routeCount"] == 0
    assert quotes["routes"] == []


def test_dex_quote_routes_do_not_apply_v2_reserve_math_to_v3_pools(monkeypatch):
    weth_usdg_pool = {
        "pool": "0x" + "7" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0Symbol": "WETH",
        "token1Symbol": "USDG",
        "balances": {"token0": 66.1894, "token1": 1_602_651, "tvlUsd": 1_765_261.05},
        "market": {"priceNative": "2456.7359", "priceUsd": "2456.73"},
        "depthBand": {"level": "deep", "label": "深度充足"},
        "hasLiquidity": True,
    }
    pons_weth_pool = {
        "pool": "0x" + "8" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0Symbol": "PONS",
        "token1Symbol": "WETH",
        "balances": {"token0": 10_253_738, "token1": 306.3267, "tvlUsd": 1_464_712.3},
        "market": {"priceNative": "0.00002810", "priceUsd": "0.06924"},
        "depthBand": {"level": "deep", "label": "深度充足"},
        "hasLiquidity": True,
    }
    pons_usdg_pool = {
        "pool": "0x" + "9" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0Symbol": "PONS",
        "token1Symbol": "USDG",
        "balances": {"token0": 10_253_738, "token1": 710_000, "tvlUsd": 1_400_000},
        "market": {"priceNative": "0.00002810", "priceUsd": "0.06924"},
        "depthBand": {"level": "deep", "label": "深度充足"},
        "hasLiquidity": True,
    }
    payload = {
        "pairs": [
            {
                "symbolPair": "WETH:USDG",
                "tokenXSymbol": "WETH",
                "tokenYSymbol": "USDG",
                "preferredPools": [weth_usdg_pool],
                "executionPools": [weth_usdg_pool],
                "strictQualityPools": [weth_usdg_pool],
            },
            {
                "symbolPair": "PONS:WETH",
                "tokenXSymbol": "PONS",
                "tokenYSymbol": "WETH",
                "preferredPools": [pons_weth_pool],
                "executionPools": [pons_weth_pool],
                "strictQualityPools": [pons_weth_pool],
            },
            {
                "symbolPair": "PONS:USDG",
                "tokenXSymbol": "PONS",
                "tokenYSymbol": "USDG",
                "preferredPools": [pons_usdg_pool],
                "executionPools": [pons_usdg_pool],
                "strictQualityPools": [pons_usdg_pool],
            },
        ],
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood", "chainId": 4663},
    }
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)
    monkeypatch.setattr(
        dex_surfaces,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {
            "protocols": [],
            "removed_protocols": [],
            "raw_snapshots": {
                "robinhood_flash_liquidity": {
                    "chainId": 4663,
                    "sources": [{"protocol": "morpho_blue", "token_symbol": "WETH", "flash_borrowable": True}],
                }
            },
        },
    )
    route_steps = [
        dex_surfaces._route_step_row(
            pons_weth_pool,
            from_token={"symbol": "WETH", "address": "0x" + "2" * 40},
            to_token={"symbol": "PONS", "address": "0x" + "8" * 40},
            direction="yc",
        ),
        dex_surfaces._route_step_row(
            pons_usdg_pool,
            from_token={"symbol": "PONS", "address": "0x" + "8" * 40},
            to_token={"symbol": "USDG", "address": "0x" + "9" * 40},
            direction="bridge",
        ),
        dex_surfaces._route_step_row(
            weth_usdg_pool,
            from_token={"symbol": "USDG", "address": "0x" + "9" * 40},
            to_token={"symbol": "WETH", "address": "0x" + "2" * 40},
            direction="zc",
        ),
    ]
    monkeypatch.setattr(
        dex_surfaces,
        "_route_framework_context_from_observation",
        lambda observation, pool_payload, *, chain_id, protocols: {
            "overview_paths": [
                {
                    "symbolPair": "WETH -> PONS -> USDG -> WETH",
                    "routeSymbols": ["WETH", "PONS", "USDG", "WETH"],
                    "routeAddresses": ["0x" + "2" * 40, "0x" + "8" * 40, "0x" + "9" * 40, "0x" + "7" * 40],
                    "routeTokenPath": [
                        {"symbol": "WETH", "address": "0x" + "2" * 40},
                        {"symbol": "PONS", "address": "0x" + "8" * 40},
                        {"symbol": "USDG", "address": "0x" + "9" * 40},
                        {"symbol": "WETH", "address": "0x" + "2" * 40},
                    ],
                    "routePools": route_steps,
                    "strictQualityPools": route_steps,
                    "preferredPools": route_steps,
                    "executionPools": route_steps,
                    "routeUsableForObservation": True,
                    "overviewRoute": {
                        "kind": "bridge_pair",
                        "anchorBorrowSymbol": "WETH",
                        "pathText": "WETH -> PONS -> USDG -> WETH",
                        "pathSymbols": ["WETH", "PONS", "USDG", "WETH"],
                        "candidatePairSymbols": ["WETH:PONS", "PONS:USDG", "USDG:WETH"],
                    },
                }
            ],
            "watch_pairs": [],
            "anchor_symbols": ["WETH"],
            "closed_loop_anchor_symbols": ["WETH"],
            "observation_anchor_symbols": ["WETH"],
            "path_source": "market_observation",
            "used_market_observation": True,
        },
    )

    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=4663)
    route = next(item for item in quotes["routes"] if item["symbolPair"] == "WETH -> PONS -> USDG -> WETH")
    analysis = route["quoteAnalysis"]

    assert route["quoteStatus"] == "requires_live_quoter"
    assert analysis["requiresLiveQuoter"] is True
    assert analysis["executableCandidate"] is False
    assert analysis["finalOutputUsd"] is None
    assert analysis["netProfitUsd"] is None
    assert analysis["steps"][0]["outputUsd"] is None
    assert all(
        str(step.get("quoteSource") or "") != "constant_product_v2_static_reserve"
        for step in analysis["steps"]
    )


def test_robinhood_testnet_live_v4_adapter_quotes_mark_live_verified(monkeypatch):
    from market.binance_market import dex_surfaces

    eth = "0x" + "0" * 40
    token_y = "0x" + "2" * 40
    pool_xy = {
        "pool": "0x" + "a" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "500",
        "token0": eth,
        "token1": token_y,
        "token0Symbol": "ETH",
        "token1Symbol": "0X71178B",
        "token0Decimals": 18,
        "token1Decimals": 18,
        "sqrtPriceX96": str(2**96),
        "tickSpacing": 60,
        "poolManager": "0x8366a39cc670b4001a1121b8f6a443a643e40951",
    }
    route = {
        "symbolPair": "ETH -> 0X71178B -> ETH",
        "routeSymbols": ["ETH", "0X71178B", "ETH"],
        "routeAddresses": [eth, token_y, eth],
        "routeTokenPath": [
            {"symbol": "ETH", "address": eth},
            {"symbol": "0X71178B", "address": token_y},
            {"symbol": "ETH", "address": eth},
        ],
        "routePools": [pool_xy],
        "preferredPools": [pool_xy],
        "strictQualityPools": [pool_xy],
        "executionPools": [pool_xy],
    }

    monkeypatch.setenv("DEX_V4_LIVE_QUOTE_ENABLED", "1")
    monkeypatch.setattr(dex_surfaces, "load_v4_sdk_adapter", lambda **kwargs: object())

    def fake_quote_v4_exact_input_single(*, adapter, request):
        return {
            "amountOutRaw": int(int(request["amountInRaw"]) * 101 / 100),
            "quoteMode": request.get("quoteMode"),
            "sdkAdapter": "mock",
            "quoteAdapter": "mock",
        }

    monkeypatch.setattr(dex_surfaces, "quote_v4_exact_input_single", fake_quote_v4_exact_input_single)

    analysis = dex_surfaces._route_quote_analysis(
        route,
        price_map={"ETH": 1.0, "0X71178B": 1.0},
        chain_id=46630,
    )

    assert analysis["livePriceVerified"] is True
    assert analysis["priceEvidenceSource"] == "chain_live_quote"
    assert analysis["priceEvidenceMode"] == "live_verified"
    assert analysis["steps"][0]["trustedUsdPrice"] is True
    assert analysis["steps"][0]["outputUsd"] is not None
    assert analysis["executableCandidate"] is True
    assert analysis["chainSubmissionCandidate"] is True


def test_robinhood_testnet_v4_pool_id_only_routes_still_use_live_adapter(monkeypatch):
    from market.binance_market import dex_surfaces

    eth = "0x" + "0" * 40
    token_y = "0x" + "2" * 40
    pool_xy = {
        "poolId": "0x" + "a" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "10000",
        "token0": eth,
        "token1": token_y,
        "token0Symbol": "ETH",
        "token1Symbol": "AMD",
        "token0Decimals": 18,
        "token1Decimals": 18,
        "sqrtPriceX96": str(2**96),
        "tickSpacing": 60,
        "poolManager": "0x8366a39cc670b4001a1121b8f6a443a643e40951",
    }
    route = {
        "routeTokenPath": [
            {"symbol": "ETH", "address": eth},
            {"symbol": "AMD", "address": token_y},
            {"symbol": "ETH", "address": eth},
        ],
        "executionPools": [pool_xy],
    }

    monkeypatch.setenv("DEX_V4_LIVE_QUOTE_ENABLED", "1")
    monkeypatch.setattr(dex_surfaces, "load_v4_sdk_adapter", lambda **kwargs: object())

    def fake_quote_v4_exact_input_single(*, adapter, request):
        assert request["pool"] == pool_xy["poolId"]
        return {
            "amountOutRaw": str(int(int(request["amountInRaw"]) * 99 / 100)),
            "quoteMode": request.get("quoteMode"),
            "sdkAdapter": "mock",
            "quoteAdapter": "mock",
        }

    monkeypatch.setattr(dex_surfaces, "quote_v4_exact_input_single", fake_quote_v4_exact_input_single)

    quote = dex_surfaces._edge_quote(
        pool=pool_xy,
        from_symbol="ETH",
        to_symbol="AMD",
        from_address=eth,
        to_address=token_y,
        input_amount=1.0,
        input_usd=1000.0,
        price_map={"ETH": 1.0, "AMD": 1.0},
        chain_id=46630,
    )

    assert quote["executionTrust"] == "live_v4_adapter"
    assert quote["quoteSource"] == "uniswap_v4_live_adapter_quote"
    assert quote["outputAmount"] is not None


def test_robinhood_testnet_quote_routes_reuses_cached_overview(monkeypatch):
    from market.binance_market import dex_surfaces

    old_cache = dict(dex_surfaces._DEX_SURFACE_CACHE)
    dex_surfaces._DEX_SURFACE_CACHE.clear()
    calls = {"overview": 0}

    overview_payload = {
        "mode": "dex_overview",
        "chain_id": 46630,
        "selected_protocols": ["uniswap_v4"],
        "summary": {"arbitragePathCount": 1},
        "decisionBasis": "robinhood_testnet_market_observation_closed_loop",
        "marketObservationSource": "cached_snapshot",
        "marketObservationSourceStage": "cached_snapshot",
        "routeFrameworkSource": "market_observation",
        "marketObservation": {"summary": {}, "chainSpecificObservationPairs": []},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "arbitragePaths": [
            {
                "symbolPair": "A -> B -> A",
                "overviewRoute": {
                    "kind": "bridge_pair",
                    "anchorBorrowSymbol": "A",
                    "bridgeSymbols": ["B"],
                    "pathSymbols": ["A", "B", "A"],
                    "pathText": "A -> B -> A",
                },
                "routeSymbols": ["A", "B", "A"],
                "routeAddresses": [],
                "routeTokenPath": [],
                "quoteAnalysis": {
                    "complete": True,
                    "requiresLiveQuoter": False,
                    "adapterRequired": False,
                    "livePriceVerified": False,
                    "priceEvidenceMode": "framework_reused_live_confirmation",
                    "priceEvidenceSource": "cached_framework_live_confirmation",
                    "profitable": False,
                    "executableCandidate": False,
                    "chainSubmissionCandidate": False,
                    "chainSubmissionPolicy": "live_framework_reuse",
                    "recommendation": {"level": "watch", "action": "watch", "reasons": []},
                },
                "quoteInputs": {"candidatePools": [], "routePools": [], "quoteRequests": []},
            }
        ],
    }

    def fake_overview_payload(**kwargs):
        calls["overview"] += 1
        return overview_payload

    monkeypatch.setattr(dex_surfaces, "dex_overview_payload", fake_overview_payload)
    monkeypatch.setattr(dex_surfaces, "dex_market_observation_surface_payload", lambda **kwargs: None)
    try:
        first = dex_surfaces.dex_quote_routes_payload(chain_id=46630, protocols=["uniswap_v4"], use_static_observation=True)
        second = dex_surfaces.dex_quote_routes_payload(chain_id=46630, protocols=["uniswap_v4"], use_static_observation=True)
    finally:
        dex_surfaces._DEX_SURFACE_CACHE.clear()
        dex_surfaces._DEX_SURFACE_CACHE.update(old_cache)

    assert calls["overview"] == 1
    assert first["summary"]["routeCount"] == 1
    assert second["summary"]["routeCount"] == 1
    assert first["routes"][0]["symbolPair"] == "A -> B -> A"
    assert first["marketObservationSource"] == "cached_snapshot"
    assert first["marketObservationSourceStage"] == "cached_snapshot"


def test_robinhood_testnet_overview_reads_database_observation(monkeypatch):
    from market.binance_market import dex_surfaces

    old_cache = dict(dex_surfaces._DEX_SURFACE_CACHE)
    dex_surfaces._DEX_SURFACE_CACHE.clear()
    calls = {"observation": 0}

    usdc = "0x" + "1" * 40
    weth = "0x" + "2" * 40
    dai = "0x" + "3" * 40
    cached_observation = {
        "mode": "dex_market_observation",
        "chain_id": 46630,
        "selected_protocols": ["uniswap_v4"],
        "marketObservationSource": "cached_surface_framework",
        "summary": {"observationSequenceCount": 1, "closedLoopRouteCount": 1},
        "chainSpecificObservationPairs": [],
        "chainSpecificObservationPools": [],
        "closedLoopRoutes": [
            {
                "kind": "bridge_closed_loop",
                "anchorSymbol": "USDC",
                "targetSymbol": "DAI",
                "closedLoop": True,
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
                    {"pool": "0x" + "a" * 40, "protocol": "uniswap_v4", "fee": 100, "routeSymbols": ["USDC", "WETH"], "routeAddresses": [usdc, weth]},
                    {"pool": "0x" + "b" * 40, "protocol": "uniswap_v4", "fee": 30, "routeSymbols": ["WETH", "DAI"], "routeAddresses": [weth, dai]},
                    {"pool": "0x" + "c" * 40, "protocol": "uniswap_v4", "fee": 5, "routeSymbols": ["DAI", "USDC"], "routeAddresses": [dai, usdc]},
                ],
                "routePairs": [
                    {"symbolPair": "USDC:WETH", "addressPair": f"{usdc}:{weth}", "preferredPools": [], "executionPools": []},
                    {"symbolPair": "WETH:DAI", "addressPair": f"{weth}:{dai}", "preferredPools": [], "executionPools": []},
                    {"symbolPair": "DAI:USDC", "addressPair": f"{dai}:{usdc}", "preferredPools": [], "executionPools": []},
                ],
            }
        ],
    }
    overview_cache_key = dex_surfaces._surface_cache_key(
        "dex_market_observation",
        chain_id=46630,
        protocols=["uniswap_v4"],
    )
    dex_surfaces._DEX_SURFACE_CACHE[overview_cache_key] = cached_observation

    def fake_market_observation_payload(**kwargs):
        calls["observation"] += 1
        return cached_observation

    monkeypatch.setattr(dex_surfaces, "dex_market_observation_payload", fake_market_observation_payload)
    monkeypatch.setattr(
        dex_surfaces,
        "_database_first_robinhood_observation_payload",
        lambda **kwargs: {
            **cached_observation,
            "marketObservationSource": "sqlite_db",
            "marketObservationSourceStage": "sqlite_db",
        },
    )
    monkeypatch.setattr(
        dex_surfaces,
        "dex_pool_candidates_payload",
        lambda chain_id=None, protocols=None: {
            "pairs": [],
            "summary": {},
            "dexSettlement": {"uniswapx": {"supported": False}},
            "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        },
    )
    monkeypatch.setattr(
        dex_surfaces,
        "_route_framework_context_from_observation",
        lambda observation, pool_payload, *, chain_id, protocols: {
            "overview_paths": [
                {
                    "symbolPair": "USDC -> WETH -> DAI -> USDC",
                    "routeSymbols": ["USDC", "WETH", "DAI", "USDC"],
                    "routeAddresses": [usdc, weth, dai, usdc],
                    "routeTokenPath": [
                        {"symbol": "USDC", "address": usdc},
                        {"symbol": "WETH", "address": weth},
                        {"symbol": "DAI", "address": dai},
                        {"symbol": "USDC", "address": usdc},
                    ],
                    "routePools": cached_observation["closedLoopRoutes"][0]["routePools"],
                    "overviewRoute": {
                        "kind": "bridge_pair",
                        "anchorBorrowSymbol": "USDC",
                        "pathSymbols": ["USDC", "WETH", "DAI", "USDC"],
                        "pathText": "USDC -> WETH -> DAI -> USDC",
                    },
                }
            ],
            "watch_pairs": [],
            "anchor_symbols": ["USDC"],
            "closed_loop_anchor_symbols": ["USDC"],
            "observation_anchor_symbols": ["USDC"],
            "path_source": "market_observation",
            "used_market_observation": True,
        },
    )
    monkeypatch.setattr(
        dex_surfaces,
        "_route_with_overview_profit",
        lambda route, **kwargs: {
            **route,
            "overviewProfit": {
                "complete": True,
                "requiresLiveQuoter": False,
                "adapterRequired": False,
                "profitable": False,
                "executableCandidate": False,
                "chainSubmissionCandidate": False,
                "referenceNetProfitUsd": 1.0,
            },
        },
    )
    try:
        overview = dex_surfaces.dex_overview_payload(chain_id=46630, protocols=["uniswap_v4"])
    finally:
        dex_surfaces._DEX_SURFACE_CACHE.clear()
        dex_surfaces._DEX_SURFACE_CACHE.update(old_cache)

    assert calls["observation"] == 0
    assert overview["routeFrameworkSource"] == "market_observation"
    assert overview["marketObservationSource"] == "sqlite_db"
    assert overview["arbitragePaths"][0]["overviewRoute"]["pathText"] == "USDC -> WETH -> DAI -> USDC"


def test_dex_overview_payload_reuses_cached_framework_payload(monkeypatch):
    from market.binance_market import dex_surfaces

    old_cache = dict(dex_surfaces._DEX_SURFACE_CACHE)
    dex_surfaces._DEX_SURFACE_CACHE.clear()
    calls = {"candidates": 0, "surface": 0}

    def fake_market_observation_surface_payload(**kwargs):
        calls["surface"] += 1
        return {
            "summary": {},
            "chainSpecificObservationPairs": [],
            "chainSpecificObservationPools": [],
            "closedLoopRoutes": [],
            "marketObservationSource": "computed",
            "marketObservationSourceStage": "computed",
        }

    def fake_candidate_payload(chain_id, protocols=None):
        calls["candidates"] += 1
        return {"pairs": [], "summary": {}, "dexSettlement": {}, "poolStaticTable": {}, "flashLoanLiquidity": {}}

    def fake_route_framework_context_from_observation(observation, pool_payload, *, chain_id, protocols):
        return {
            "overview_paths": [
                {
                    "overviewRoute": {
                        "pathText": "USDC -> WETH -> DAI -> USDC",
                        "kind": "bridge_pair",
                        "candidatePairSymbols": ["USDC:WETH"],
                    },
                    "routeSymbols": ["USDC", "WETH", "DAI", "USDC"],
                }
            ],
            "watch_pairs": [],
            "anchor_symbols": ["USDC"],
            "closed_loop_anchor_symbols": ["USDC"],
            "observation_anchor_symbols": ["USDC"],
            "path_source": "market_observation",
            "used_market_observation": True,
        }

    try:
        monkeypatch.setattr(dex_surfaces, "dex_market_observation_surface_payload", fake_market_observation_surface_payload)
        monkeypatch.setattr(dex_surfaces, "_candidate_payload", fake_candidate_payload)
        monkeypatch.setattr(dex_surfaces, "_route_framework_context_from_observation", fake_route_framework_context_from_observation)
        monkeypatch.setattr(dex_surfaces, "_route_with_overview_profit", lambda route, **kwargs: route)

        first = dex_surfaces.dex_overview_payload(chain_id=46630, protocols=["uniswap_v4"])
        second = dex_surfaces.dex_overview_payload(chain_id=46630, protocols=["uniswap_v4"])
    finally:
        dex_surfaces._DEX_SURFACE_CACHE.clear()
        dex_surfaces._DEX_SURFACE_CACHE.update(old_cache)

    assert calls == {"candidates": 0, "surface": 0}
    assert first == second
    assert second["marketObservationSourceStage"] == "sqlite_db"
    assert second["tableSource"] == "dex_table"
    assert second["referencedTable"] == "ob_table"
    assert second["arbitragePaths"][0]["pathSource"] == "market_observation"


def test_robinhood_testnet_overview_builds_from_database_candidate_pools(monkeypatch):
    calls = {"candidate": 0, "context": 0}

    def fake_candidate_payload(chain_id, protocols=None):
        calls["candidate"] += 1
        assert chain_id == 46630
        assert protocols == ["uniswap_v4"]
        return {
            "pairs": [],
            "summary": {},
            "dexSettlement": {},
            "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630, "exists": True, "generatedAt": "2026-01-01T00:00:00Z"},
            "flashLoanLiquidity": {"available_protocols": ["venus_core"]},
        }

    def fake_context(observation, pool_payload, **kwargs):
        calls["context"] += 1
        assert pool_payload is None
        return {
            "overview_paths": [],
            "watch_pairs": [],
            "anchor_symbols": ["ETH"],
            "closed_loop_anchor_symbols": ["ETH"],
            "observation_anchor_symbols": ["ETH"],
            "path_source": "market_observation",
            "used_market_observation": True,
        }

    monkeypatch.setattr(dex_surfaces, "_candidate_payload", fake_candidate_payload)
    monkeypatch.setattr(dex_surfaces, "_route_framework_context_from_observation", fake_context)

    overview = dex_surfaces.dex_overview_payload(
        chain_id=46630,
        protocols=["uniswap_v4"],
        use_static_observation=True,
    )

    # Robinhood testnet V4 is database-first: the overview reads the same
    # candidate tables instead of falling back to a static compact snapshot.
    assert calls == {"candidate": 0, "context": 1}
    assert overview["routeFrameworkSource"] == "market_observation"
    assert overview["summary"]["arbitragePathCount"] == 0
    assert overview["arbitragePaths"] == []


def test_robinhood_mainnet_overview_builds_from_database_candidate_pools(monkeypatch):
    compact_snapshot = {
        "exists": True,
        "chain_id": 4663,
        "protocols": ["uniswap_v4"],
        "snapshot_kind": "dex_overview_compact",
        "summary": {"closedLoopRouteCount": 0},
        "closedLoopRouteSummaries": [],
        "chainSpecificObservationPairs": [],
        "poolStaticTable": {"network": "robinhood", "chainId": 4663, "exists": True},
        "flashLoanLiquidity": {"available_protocols": ["morpho_blue"]},
    }
    observation_snapshot = {
        "exists": True,
        "chain_id": 4663,
        "protocols": ["uniswap_v4"],
        "summary": {"closedLoopRouteCount": 0},
        "observationSequences": [],
        "chainSpecificObservationPairs": [],
        "poolStaticTable": {"network": "robinhood", "chainId": 4663, "exists": True},
        "flashLoanLiquidity": {"available_protocols": ["morpho_blue"]},
    }
    calls = {"candidate": 0, "context": 0}

    def fake_candidate_payload(chain_id, protocols=None):
        calls["candidate"] += 1
        assert chain_id == 4663
        assert protocols == ["uniswap_v4"]
        return {
            "pairs": [],
            "summary": {},
            "dexSettlement": {},
            "poolStaticTable": {"network": "robinhood", "chainId": 4663, "exists": True, "generatedAt": "2026-01-01T00:00:00Z"},
            "flashLoanLiquidity": {"available_protocols": ["morpho_blue"]},
        }

    def fake_context(observation, pool_payload, **kwargs):
        calls["context"] += 1
        assert pool_payload is None
        return {
            "overview_paths": [],
            "watch_pairs": [],
            "anchor_symbols": ["ETH"],
            "closed_loop_anchor_symbols": ["ETH"],
            "observation_anchor_symbols": ["ETH"],
            "path_source": "market_observation",
            "used_market_observation": True,
        }

    monkeypatch.setattr(dex_surfaces, "load_market_observation_overview_static", lambda **kwargs: compact_snapshot)
    monkeypatch.setattr(dex_surfaces, "load_market_observation_static", lambda **kwargs: observation_snapshot)
    monkeypatch.setattr(dex_surfaces, "_candidate_payload", fake_candidate_payload)
    monkeypatch.setattr(dex_surfaces, "_route_framework_context_from_observation", fake_context)

    overview = dex_surfaces.dex_overview_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        use_static_observation=True,
    )

    # Robinhood mainnet V4 is database-first: the overview reads the same
    # candidate tables instead of falling back to a static compact snapshot.
    # `_candidate_payload` is now a SQLite read, not a rebuild/fallback.
    assert calls == {"candidate": 0, "context": 1}
    assert overview["routeFrameworkSource"] == "market_observation"
    assert overview["summary"]["arbitragePathCount"] == 0
    assert overview["arbitragePaths"] == []


def test_quote_routes_consume_overview_without_rebuilding_closed_loops(monkeypatch):
    overview = {
        "arbitragePaths": [
            {
                "symbolPair": "USDC -> WETH -> DAI -> USDC",
                "routeSymbols": ["USDC", "WETH", "DAI", "USDC"],
                "routeAddresses": ["0xusdc", "0xweth", "0xdai", "0xusdc"],
                "routeTokenPath": [
                    {"symbol": "USDC", "address": "0xusdc"},
                    {"symbol": "WETH", "address": "0xweth"},
                    {"symbol": "DAI", "address": "0xdai"},
                    {"symbol": "USDC", "address": "0xusdc"},
                ],
                "overviewRoute": {
                    "kind": "bridge_pair",
                    "pathSymbols": ["USDC", "WETH", "DAI", "USDC"],
                    "pathText": "USDC -> WETH -> DAI -> USDC",
                    "candidatePairSymbols": ["USDC:WETH"],
                },
                "routeUsableForObservation": True,
                "executionPools": [
                    {
                        "pool": "0x" + "a" * 64,
                        "protocol": "uniswap_v4",
                        "dex": "uniswap_v4",
                        "fee": 500,
                        "hasLiquidity": True,
                        "liquidityIndex": 100.0,
                        "balances": {"tvlUsd": 100_000},
                    }
                ],
            }
        ],
        "summary": {
            "routeCount": 1,
            "marketObservationRouteCount": 1,
            "hopCandidateRouteCount": 0,
            "hopCandidateCombinationCount": 1,
        },
        "marketObservation": {"summary": {}, "chainSpecificObservationPairs": []},
        "routeFrameworkSource": "market_observation",
        "marketObservationSource": "cached_snapshot",
        "marketObservationSourceStage": "cached_snapshot",
        "tableSource": "dex_table",
        "referencedTable": "ob_table",
        "poolIndexVersion": {},
        "dexSettlement": {},
        "poolStaticTable": {},
    }

    calls = {"overview": 0, "context": 0}

    def fake_overview_payload(**kwargs):
        calls["overview"] += 1
        return overview

    def fail_context(*args, **kwargs):
        calls["context"] += 1
        raise AssertionError("quote/routing must not rebuild closed loops from observation")

    monkeypatch.setattr(dex_surfaces, "dex_overview_payload", fake_overview_payload)
    monkeypatch.setattr(dex_surfaces, "_route_framework_context_from_observation", fail_context)

    payload = dex_surfaces.dex_quote_routes_payload(
        chain_id=46630,
        protocols=["uniswap_v4"],
    )

    assert calls == {"overview": 1, "context": 0}
    assert len(payload["routes"]) == 1
    assert payload["routes"][0]["symbolPair"] == "USDC -> WETH -> DAI -> USDC"
    assert payload["summary"]["pathSource"] == "market_observation"


def test_quote_routes_keep_non_usable_liquidity_routes_for_selection(monkeypatch):
    from market.binance_market.dex_quote_surface import build_quote_routes_payload_from_overview

    usable_route = {
        "symbolPair": "ETH -> PEPE -> USDG -> ETH",
        "routeSymbols": ["ETH", "PEPE", "USDG", "ETH"],
        "routeAddresses": ["0x0", "0xaa", "0xbb", "0x0"],
        "overviewRoute": {"pathText": "ETH -> PEPE -> USDG -> ETH", "candidatePairSymbols": ["ETH:PEPE"]},
        "routeUsableForObservation": True,
        "executionPools": [
            {
                "pool": "0x" + "a" * 64,
                "protocol": "uniswap_v4",
                "dex": "uniswap_v4",
                "fee": 500,
                "specialFee": False,
                "dynamicFee": False,
                "hasLiquidity": True,
                "liquidityIndex": 100.0,
                "balances": {"tvlUsd": 100_000},
                "depthBand": {"label": "deep"},
            }
        ],
    }
    unusable_route = dict(usable_route)
    unusable_route["symbolPair"] = "ETH -> DIG -> USDG -> ETH"
    unusable_route["routeSymbols"] = ["ETH", "DIG", "USDG", "ETH"]
    unusable_route["routeUsableForObservation"] = False
    unusable_route["executionPools"] = [
        {
            "pool": "0x" + "b" * 64,
            "protocol": "uniswap_v4",
            "dex": "uniswap_v4",
            "fee": 500,
            "specialFee": True,
            "dynamicFee": False,
            "hasLiquidity": False,
            "balances": {"tvlUsd": 0},
            "depthBand": {"label": "indexed V4 PoolKey"},
        }
    ]
    overview = {
        "arbitragePaths": [unusable_route, usable_route],
        "summary": {},
        "marketObservation": {},
        "dexSettlement": {},
        "poolStaticTable": {},
        "routeFrameworkSource": "candidate_pool_fallback",
    }
    payload = build_quote_routes_payload_from_overview(
        overview,
        chain_id=4663,
        protocols=["uniswap_v4"],
        route_quote_analysis_fn=lambda *args, **kwargs: {},
        route_size_analysis_fn=lambda *args, **kwargs: {},
    )
    assert payload["summary"]["unfilteredRouteCount"] == 2
    assert payload["summary"]["routeCount"] == 2
    assert {route["symbolPair"] for route in payload["routes"]} == {
        "ETH -> PEPE -> USDG -> ETH",
        "ETH -> DIG -> USDG -> ETH",
    }
    by_symbol = {route["symbolPair"]: route for route in payload["routes"]}
    assert by_symbol["ETH -> PEPE -> USDG -> ETH"]["routeUsableForObservation"] is True
    assert by_symbol["ETH -> DIG -> USDG -> ETH"]["routeUsableForObservation"] is False
    assert payload["routes"][0]["pathSource"] == "candidate_pool_fallback"
    assert payload["routes"][0]["chainReviewEligible"] is False
    assert payload["routes"][0]["diagnosticOnly"] is True


