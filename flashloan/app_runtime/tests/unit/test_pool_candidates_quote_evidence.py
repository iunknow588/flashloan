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


def test_v4_sqrt_price_reference_quote_completes_static_measurement():
    usdt = "0x" + "1" * 40
    eth = "0x" + "0" * 40
    pool = {
        "pool": "0x" + "2" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "0",
        "token0": usdt,
        "token1": eth,
        "token0Symbol": "USDT",
        "token1Symbol": "ETH",
        "token0Decimals": 18,
        "token1Decimals": 18,
        "sqrtPriceX96": str(2**96),
        "balances": {"token0": 1_000_000, "token1": 1_000_000, "tvlUsd": None},
    }
    route = {
        "routeTokenPath": [
            {"symbol": "USDT", "address": usdt},
            {"symbol": "ETH", "address": eth},
            {"symbol": "USDT", "address": usdt},
        ],
        "executionPools": [pool],
    }

    analysis = dex_surfaces._route_quote_analysis(route, price_map={"USDT": 1.0, "ETH": 1.0})

    assert analysis["complete"] is True
    assert analysis["requiresLiveQuoter"] is True
    assert analysis["adapterRequired"] is True
    assert analysis["finalOutputUsd"] is None
    assert analysis["referenceFinalOutputUsd"] == 1000.0
    assert analysis["staticDepthStatus"] == "available"
    assert "static_depth_evidence_unavailable" not in analysis["warnings"]
    assert analysis["referenceGrossProfitUsd"] == 0.0
    assert analysis["referenceNetProfitUsd"] == -1.5
    assert len(analysis["steps"]) == 2
    assert all(step["referenceOutputUsd"] == 1000.0 for step in analysis["steps"])
    assert all(step["quoteSource"] == "uniswap_v4_sdk_adapter_required" for step in analysis["steps"])


def test_v4_price_map_infers_unknown_token_from_usd_anchor_pool():
    usdt = "0x" + "1" * 40
    token = "0x" + "2" * 40
    pool = {
        "pool": "0x" + "9" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "0",
        "token0": usdt,
        "token1": token,
        "token0Symbol": "USDT",
        "token1Symbol": "NGK",
        "token0Decimals": 18,
        "token1Decimals": 18,
        "sqrtPriceX96": str(2**96),
        "balances": {"token0": 1000.0, "token1": 2000.0, "tvlUsd": 3000.0},
    }
    pairs = [{"symbolPair": "USDT:NGK", "preferredPools": [pool]}]

    price_map = dex_surfaces._price_map_from_pairs(pairs)

    assert price_map["USDT"] == 1.0
    assert price_map["NGK"] == pytest.approx(0.5)


def test_v4_reference_route_without_usd_anchor_does_not_create_fake_profit():
    eth = "0x" + "0" * 40
    token_y = "0x71178bac73cbeb415514eb542a8995b82669778d"
    token_z = "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e"
    pool_xy = {
        "pool": "0x" + "a" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "10000",
        "token0": eth,
        "token1": token_y,
        "token0Symbol": "ETH",
        "token1Symbol": "0X71178B",
        "sqrtPriceX96": "560227709747861399187319382274",
    }
    pool_yz = {
        "pool": "0x" + "b" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "3000",
        "token0": token_y,
        "token1": token_z,
        "token0Symbol": "0X71178B",
        "token1Symbol": "0XC9F9C8",
        "sqrtPriceX96": "112045541949572279837463876454",
    }
    pool_zx = {
        "pool": "0x" + "c" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "500",
        "token0": eth,
        "token1": token_z,
        "token0Symbol": "ETH",
        "token1Symbol": "0XC9F9C8",
        "sqrtPriceX96": str(2**96),
    }
    pairs = [
        {"symbolPair": "ETH:0X71178B", "preferredPools": [pool_xy]},
        {"symbolPair": "0X71178B:0XC9F9C8", "preferredPools": [pool_yz]},
        {"symbolPair": "ETH:0XC9F9C8", "preferredPools": [pool_zx]},
    ]
    route = {
        "routeTokenPath": [
            {"symbol": "ETH", "address": eth},
            {"symbol": "0X71178B", "address": token_y},
            {"symbol": "0XC9F9C8", "address": token_z},
            {"symbol": "ETH", "address": eth},
        ],
        "executionPools": [pool_xy, pool_yz, pool_zx],
    }

    price_map = dex_surfaces._price_map_from_pairs(pairs)
    analysis = dex_surfaces._route_quote_analysis(route, price_map=price_map)

    assert "ETH" not in price_map
    assert analysis["complete"] is False
    assert analysis["stepCount"] == 1
    assert analysis["requiresLiveQuoter"] is True
    assert analysis["adapterRequired"] is True
    assert analysis["trustedUsdReference"] is False
    assert analysis["profitable"] is False
    assert analysis["referenceFinalOutputUsd"] is None
    assert analysis["referenceNetProfitUsd"] is None
    assert analysis["referenceFinalOutputAmount"] is None
    assert analysis["referenceNetReturnBps"] is None
    assert analysis["tokenBasisReferenceProfitable"] is False
    assert analysis["tokenBasisProfitable"] is False
    assert analysis["chainSubmissionCandidate"] is False
    assert analysis["referenceReturnTrusted"] is False
    assert analysis["recommendation"]["level"] == "blocked"
    assert analysis["recommendation"]["action"] == "等待真实报价"
    assert "reference_price_step_requires_live_quoter" in analysis["warnings"]
    assert "route_truncated_at_hop_1_requires_live_quoter" in analysis["warnings"]




def test_robinhood_testnet_requires_live_quoter_before_submission_candidate(monkeypatch):
    eth = "0x" + "0" * 40
    token_y = "0x" + "2" * 40
    token_z = "0x" + "3" * 40
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
    }
    pool_yz = {
        "pool": "0x" + "b" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "3000",
        "token0": token_y,
        "token1": token_z,
        "token0Symbol": "0X71178B",
        "token1Symbol": "0XC9F9C8",
        "token0Decimals": 18,
        "token1Decimals": 18,
        "sqrtPriceX96": "112045541949572279837463876454",
    }
    pool_zx = {
        "pool": "0x" + "c" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "500",
        "token0": eth,
        "token1": token_z,
        "token0Symbol": "ETH",
        "token1Symbol": "0XC9F9C8",
        "token0Decimals": 18,
        "token1Decimals": 18,
        "sqrtPriceX96": str(2**96),
    }
    pairs = [
        {"symbolPair": "ETH:0X71178B", "preferredPools": [pool_xy]},
        {"symbolPair": "0X71178B:0XC9F9C8", "preferredPools": [pool_yz]},
        {"symbolPair": "ETH:0XC9F9C8", "preferredPools": [pool_zx]},
    ]
    route = {
        "routeTokenPath": [
            {"symbol": "ETH", "address": eth},
            {"symbol": "0X71178B", "address": token_y},
            {"symbol": "0XC9F9C8", "address": token_z},
            {"symbol": "ETH", "address": eth},
        ],
        "executionPools": [pool_xy, pool_yz, pool_zx],
    }

    price_map = dex_surfaces._price_map_from_pairs(pairs)
    monkeypatch.setattr(dex_surfaces, "load_v4_sdk_adapter", lambda **kwargs: object())

    def fake_quote_v4_exact_input_single(*, adapter, request):
        return {
            "amountOutRaw": str(int(int(request["amountInRaw"]) * 10005 / 10000)),
            "quoteMode": request.get("quoteMode"),
            "sdkAdapter": "mock",
            "quoteAdapter": "mock",
        }

    monkeypatch.setattr(dex_surfaces, "quote_v4_exact_input_single", fake_quote_v4_exact_input_single)
    analysis = dex_surfaces._route_quote_analysis(route, price_map=price_map, chain_id=46630)

    # The Robinhood testnet path is live-enabled by default.  The mocked
    # adapter only replaces the network call; it still represents a complete
    # live quote for route-analysis purposes.
    assert analysis["requiresLiveQuoter"] is False
    assert analysis["adapterRequired"] is False
    assert analysis["chainSubmissionCandidate"] is True
    assert analysis["executableCandidate"] is False
    assert analysis["tokenBasisReferenceProfitable"] is True
    assert analysis["staticDepthStatus"] == "available"


def test_robinhood_testnet_static_reference_survives_missing_token_decimals(monkeypatch):
    eth = "0x" + "0" * 40
    token_y = "0x" + "2" * 40
    token_z = "0x" + "3" * 40
    pool_xy = {
        "pool": "0x" + "a" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "10000",
        "token0": eth,
        "token1": token_y,
        "token0Symbol": "ETH",
        "token1Symbol": "0X71178B",
        "sqrtPriceX96": str(2**96),
    }
    pool_yz = {
        "pool": "0x" + "b" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "3000",
        "token0": token_y,
        "token1": token_z,
        "token0Symbol": "0X71178B",
        "token1Symbol": "0XC9F9C8",
        "sqrtPriceX96": "112045541949572279837463876454",
    }
    pool_zx = {
        "pool": "0x" + "c" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "500",
        "token0": eth,
        "token1": token_z,
        "token0Symbol": "ETH",
        "token1Symbol": "0XC9F9C8",
        "sqrtPriceX96": str(2**96),
    }
    route = {
        "routeTokenPath": [
            {"symbol": "ETH", "address": eth},
            {"symbol": "0X71178B", "address": token_y},
            {"symbol": "0XC9F9C8", "address": token_z},
            {"symbol": "ETH", "address": eth},
        ],
        "executionPools": [pool_xy, pool_yz, pool_zx],
    }

    monkeypatch.setattr(dex_surfaces, "load_v4_sdk_adapter", lambda **kwargs: object())
    monkeypatch.setenv("DEX_V4_LIVE_QUOTE_ENABLED", "1")
    monkeypatch.setattr(dex_surfaces, "_v4_live_quote_enabled", lambda: True)

    def fake_zero_quote(*, adapter, request):
        return {
            "amountOutRaw": "0",
            "quoteMode": request.get("quoteMode"),
            "sdkAdapter": "mock",
            "quoteAdapter": "mock",
        }

    monkeypatch.setattr(dex_surfaces, "quote_v4_exact_input_single", fake_zero_quote)
    analysis = dex_surfaces._route_quote_analysis(route, price_map={}, chain_id=46630)

    assert analysis["complete"] is False
    assert analysis["stepCount"] == 1
    assert analysis["tokenDecimalsTrusted"] is False
    assert analysis["requiresLiveQuoter"] is True
    assert analysis["adapterRequired"] is True
    assert analysis["chainSubmissionCandidate"] is False
    assert analysis["executableCandidate"] is False
    assert analysis["referenceFinalOutputUsd"] is None
    assert analysis["referenceNetProfitUsd"] is None
    assert analysis["tokenBasisReferenceProfitable"] is False
    assert "v4_token_decimals_missing_requires_metadata" in analysis["warnings"]


def test_robinhood_testnet_v4_liquidity_failure_preserves_quoter_error(monkeypatch):
    eth = "0x" + "0" * 40
    token_y = "0x" + "2" * 40
    token_z = "0x" + "3" * 40
    pools = [
        {
            "pool": "0x" + "a" * 64,
            "dex": "uniswap_v4",
            "protocol": "uniswap_v4",
            "fee": "10000",
            "token0": eth,
            "token1": token_y,
            "token0Symbol": "ETH",
            "token1Symbol": "0X71178B",
            "token0Decimals": 18,
            "token1Decimals": 18,
            "sqrtPriceX96": str(2**96),
        },
        {
            "pool": "0x" + "b" * 64,
            "dex": "uniswap_v4",
            "protocol": "uniswap_v4",
            "fee": "3000",
            "token0": token_y,
            "token1": token_z,
            "token0Symbol": "0X71178B",
            "token1Symbol": "0XC9F9C8",
            "token0Decimals": 18,
            "token1Decimals": 18,
            "sqrtPriceX96": str(2**96),
        },
        {
            "pool": "0x" + "c" * 64,
            "dex": "uniswap_v4",
            "protocol": "uniswap_v4",
            "fee": "500",
            "token0": token_z,
            "token1": eth,
            "token0Symbol": "0XC9F9C8",
            "token1Symbol": "ETH",
            "token0Decimals": 18,
            "token1Decimals": 18,
            "sqrtPriceX96": str(2**96),
        },
    ]
    route = {
        "routeTokenPath": [
            {"symbol": "ETH", "address": eth},
            {"symbol": "0X71178B", "address": token_y},
            {"symbol": "0XC9F9C8", "address": token_z},
            {"symbol": "ETH", "address": eth},
        ],
        "executionPools": pools,
    }
    monkeypatch.setattr(dex_surfaces, "load_v4_sdk_adapter", lambda **kwargs: object())
    monkeypatch.setattr(dex_surfaces, "_v4_live_quote_enabled", lambda: True)
    quote_calls = 0

    def fake_quote(*, adapter, request):
        nonlocal quote_calls
        quote_calls += 1
        if quote_calls == 2:
            raise RuntimeError("UnexpectedRevertBytes -> NotEnoughLiquidity")
        return {
            "amountOutRaw": str(int(request["amountInRaw"])),
            "quoteMode": request.get("quoteMode"),
            "sdkAdapter": "mock",
            "quoteAdapter": "mock",
        }

    monkeypatch.setattr(dex_surfaces, "quote_v4_exact_input_single", fake_quote)
    analysis = dex_surfaces._route_quote_analysis(route, price_map={}, chain_id=46630)

    assert analysis["complete"] is False
    assert analysis["stepCount"] == 2
    assert analysis["requiresLiveQuoter"] is True
    assert analysis["adapterRequired"] is False
    assert analysis["steps"][-1]["executionTrust"] == "not_enough_liquidity"
    assert "NotEnoughLiquidity" in analysis["steps"][-1]["liveQuoteError"]
    assert "hop_2_not_enough_liquidity" in analysis["warnings"]
    assert any("NotEnoughLiquidity" in reason for reason in analysis["recommendation"]["reasons"])


def test_dex_quote_routes_drop_polluted_bridge_symbols(monkeypatch):
    usdg_weth_pool = {
        "pool": "0x" + "a" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0Symbol": "USDG",
        "token1Symbol": "WETH",
        "balances": {"token0": 1_000_000, "token1": 400, "tvlUsd": 1_000_000},
        "market": {"priceNative": "0.0004", "priceUsd": "2500"},
        "depthBand": {"level": "deep", "label": "深度充足"},
    }
    weth_robinhood_pool = {
        "pool": "0x" + "b" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0Symbol": "WETH",
        "token1Symbol": "ROBINHOOD",
        "balances": {"token0": 400, "token1": 10_000_000, "tvlUsd": 1_000_000},
        "market": {"priceNative": "25000", "priceUsd": "2500"},
        "depthBand": {"level": "deep", "label": "深度充足"},
    }
    robinhood_usdg_pool = {
        "pool": "0x" + "c" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0Symbol": "ROBINHOOD",
        "token1Symbol": "USDG",
        "balances": {"token0": 10_000_000, "token1": 900_000, "tvlUsd": 1_000_000},
        "market": {"priceNative": "0.00009", "priceUsd": "0.00009"},
        "depthBand": {"level": "deep", "label": "深度充足"},
    }
    payload = {
        "pairs": [
            {
                "symbolPair": "USDG:WETH",
                "tokenXSymbol": "USDG",
                "tokenYSymbol": "WETH",
                "preferredPools": [usdg_weth_pool],
                "executionPools": [usdg_weth_pool],
                "strictQualityPools": [usdg_weth_pool],
            },
            {
                "symbolPair": "WETH:ROBINHOOD",
                "tokenXSymbol": "WETH",
                "tokenYSymbol": "ROBINHOOD",
                "preferredPools": [weth_robinhood_pool],
                "executionPools": [weth_robinhood_pool],
                "strictQualityPools": [weth_robinhood_pool],
            },
            {
                "symbolPair": "ROBINHOOD:USDG",
                "tokenXSymbol": "ROBINHOOD",
                "tokenYSymbol": "USDG",
                "preferredPools": [robinhood_usdg_pool],
                "executionPools": [robinhood_usdg_pool],
                "strictQualityPools": [robinhood_usdg_pool],
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
    # This test only exercises route filtering. Keep the route builder on the
    # static reference path so it never dials a real Robinhood RPC endpoint
    # through the V4 live-quote adapter.
    monkeypatch.setattr(
        dex_surfaces,
        "_v4_live_quote_enabled",
        lambda chain_id=None: False,
    )
    monkeypatch.setattr(
        "market.binance_market.dex_surface_route_quotes._v4_live_quote_enabled",
        lambda chain_id=None: False,
    )

    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=4663)

    assert all("ROBINHOOD" not in route["symbolPair"] for route in quotes["routes"])
    assert all("ROBINHOOD" not in " -> ".join(route["routeSymbols"]) for route in quotes["routes"])
    assert quotes["summary"]["routeCount"] == len(quotes["routes"])


def test_robinhood_pool_candidates_split_same_symbol_by_token_address(monkeypatch):
    rows = [
        {
            "pool": "0x" + "1" * 40,
            "dex": "uniswap",
            "protocol": "uniswap_v3",
            "token0": "0x" + "a" * 40,
            "token1": "0x" + "b" * 40,
            "token0Symbol": "WETH",
            "token1Symbol": "VLAD",
            "symbolPair": "WETH:VLAD",
            "quality": "pass",
            "tvlUsd": 100_000,
        },
        {
            "pool": "0x" + "2" * 40,
            "dex": "uniswap",
            "protocol": "uniswap_v3",
            "token0": "0x" + "a" * 40,
            "token1": "0x" + "c" * 40,
            "token0Symbol": "WETH",
            "token1Symbol": "VLAD",
            "symbolPair": "WETH:VLAD",
            "quality": "pass",
            "tvlUsd": 200_000,
        },
    ]
    monkeypatch.setattr(pool_candidates, "_robinhood_pool_rows_and_source", lambda table, path: (rows, path, "test"))

    payload = pool_candidates._robinhood_pool_table_payload(
        {"schemaVersion": "robinhood-test", "network": "robinhood", "chainId": 4663},
        pool_candidates.ROBINHOOD_DEX_CANDIDATE_PATH,
        source="test",
        fallback_reason=None,
    )

    vlad_pairs = [pair for pair in payload["pairs"] if pair["symbolPair"] == "WETH:VLAD"]
    assert len(vlad_pairs) == 2
    assert {pair["addressPair"] for pair in vlad_pairs} == {
        "0x" + "a" * 40 + ":" + "0x" + "b" * 40,
        "0x" + "a" * 40 + ":" + "0x" + "c" * 40,
    }
    assert all(pair["identityMode"] == "address" for pair in vlad_pairs)


def test_dex_quote_routes_keep_same_symbol_token_addresses_separate(monkeypatch):
    usdg = "0x" + "d" * 40
    weth = "0x" + "a" * 40
    vlad_a = "0x" + "b" * 40
    vlad_b = "0x" + "c" * 40
    usdg_weth_pool = {
        "pool": "0x" + "1" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v2",
        "fee": "v2",
        "token0": usdg,
        "token1": weth,
        "token0Symbol": "USDG",
        "token1Symbol": "WETH",
        "balances": {"token0": 1_000_000, "token1": 400, "tvlUsd": 1_000_000},
        "market": {"priceNative": "0.0004", "priceUsd": "2500"},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    weth_vlad_a_pool = {
        "pool": "0x" + "2" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0": weth,
        "token1": vlad_a,
        "token0Symbol": "WETH",
        "token1Symbol": "VLAD",
        "balances": {"token0": 400, "token1": 10_000_000, "tvlUsd": 1_000_000},
        "market": {"priceNative": "25000", "priceUsd": "2500"},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    weth_vlad_b_pool = {
        "pool": "0x" + "3" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0": weth,
        "token1": vlad_b,
        "token0Symbol": "WETH",
        "token1Symbol": "VLAD",
        "balances": {"token0": 400, "token1": 20_000_000, "tvlUsd": 1_200_000},
        "market": {"priceNative": "50000", "priceUsd": "2500"},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    vlad_a_usdg_pool = {
        "pool": "0x" + "4" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0": vlad_a,
        "token1": usdg,
        "token0Symbol": "VLAD",
        "token1Symbol": "USDG",
        "balances": {"token0": 10_000_000, "token1": 1_000_000, "tvlUsd": 1_000_000},
        "market": {"priceNative": "0.1", "priceUsd": "0.1"},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    vlad_b_usdg_pool = {
        "pool": "0x" + "5" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0": vlad_b,
        "token1": usdg,
        "token0Symbol": "VLAD",
        "token1Symbol": "USDG",
        "balances": {"token0": 20_000_000, "token1": 1_000_000, "tvlUsd": 1_200_000},
        "market": {"priceNative": "0.05", "priceUsd": "0.05"},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    payload = {
        "pairs": [
            {
                "symbolPair": "USDG:WETH",
                "addressPair": f"{usdg}:{weth}",
                "tokenX": usdg,
                "tokenY": weth,
                "tokenXSymbol": "USDG",
                "tokenYSymbol": "WETH",
                "preferredPools": [usdg_weth_pool],
                "executionPools": [usdg_weth_pool],
                "strictQualityPools": [usdg_weth_pool],
            },
            {
                "symbolPair": "WETH:VLAD",
                "addressPair": f"{weth}:{vlad_a}",
                "tokenX": weth,
                "tokenY": vlad_a,
                "tokenXSymbol": "WETH",
                "tokenYSymbol": "VLAD",
                "preferredPools": [weth_vlad_a_pool],
                "executionPools": [weth_vlad_a_pool],
                "strictQualityPools": [weth_vlad_a_pool],
            },
            {
                "symbolPair": "WETH:VLAD",
                "addressPair": f"{weth}:{vlad_b}",
                "tokenX": weth,
                "tokenY": vlad_b,
                "tokenXSymbol": "WETH",
                "tokenYSymbol": "VLAD",
                "preferredPools": [weth_vlad_b_pool],
                "executionPools": [weth_vlad_b_pool],
                "strictQualityPools": [weth_vlad_b_pool],
            },
            {
                "symbolPair": "USDG:VLAD",
                "addressPair": f"{usdg}:{vlad_a}",
                "tokenX": usdg,
                "tokenY": vlad_a,
                "tokenXSymbol": "USDG",
                "tokenYSymbol": "VLAD",
                "preferredPools": [vlad_a_usdg_pool],
                "executionPools": [vlad_a_usdg_pool],
                "strictQualityPools": [vlad_a_usdg_pool],
            },
            {
                "symbolPair": "USDG:VLAD",
                "addressPair": f"{usdg}:{vlad_b}",
                "tokenX": usdg,
                "tokenY": vlad_b,
                "tokenXSymbol": "USDG",
                "tokenYSymbol": "VLAD",
                "preferredPools": [vlad_b_usdg_pool],
                "executionPools": [vlad_b_usdg_pool],
                "strictQualityPools": [vlad_b_usdg_pool],
            },
        ],
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood", "chainId": 4663},
        "anchorGroups": build_anchor_candidate_groups(
            [
                {
                    "symbolPair": "USDG:WETH",
                    "tokenX": usdg,
                    "tokenY": weth,
                    "tokenXSymbol": "USDG",
                    "tokenYSymbol": "WETH",
                    "preferredPools": [usdg_weth_pool],
                    "strictQualityPools": [usdg_weth_pool],
                    "executionPools": [usdg_weth_pool],
                },
                {
                    "symbolPair": "WETH:VLAD",
                    "tokenX": weth,
                    "tokenY": vlad_a,
                    "tokenXSymbol": "WETH",
                    "tokenYSymbol": "VLAD",
                    "preferredPools": [weth_vlad_a_pool],
                    "strictQualityPools": [weth_vlad_a_pool],
                    "executionPools": [weth_vlad_a_pool],
                },
                {
                    "symbolPair": "WETH:VLAD",
                    "tokenX": weth,
                    "tokenY": vlad_b,
                    "tokenXSymbol": "WETH",
                    "tokenYSymbol": "VLAD",
                    "preferredPools": [weth_vlad_b_pool],
                    "strictQualityPools": [weth_vlad_b_pool],
                    "executionPools": [weth_vlad_b_pool],
                },
                {
                    "symbolPair": "USDG:VLAD",
                    "tokenX": usdg,
                    "tokenY": vlad_a,
                    "tokenXSymbol": "USDG",
                    "tokenYSymbol": "VLAD",
                    "preferredPools": [vlad_a_usdg_pool],
                    "strictQualityPools": [vlad_a_usdg_pool],
                    "executionPools": [vlad_a_usdg_pool],
                },
                {
                    "symbolPair": "USDG:VLAD",
                    "tokenX": usdg,
                    "tokenY": vlad_b,
                    "tokenXSymbol": "USDG",
                    "tokenYSymbol": "VLAD",
                    "preferredPools": [vlad_b_usdg_pool],
                    "strictQualityPools": [vlad_b_usdg_pool],
                    "executionPools": [vlad_b_usdg_pool],
                },
            ],
            anchor_symbols=["WETH", "USDG"],
        ),
    }
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)
    monkeypatch.setattr(
        dex_surfaces,
        "_v4_index_bridge_pairs_for_anchor_groups",
        lambda *args, **kwargs: ([], {"enabled": False, "status": "test_stub"}),
    )

    def db_step(pool, from_symbol, from_address, to_symbol, to_address):
        return {
            **pool,
            "anchorSymbol": from_symbol,
            "anchorAddress": from_address,
            "counterSymbol": to_symbol,
            "counterAddress": to_address,
            "routeSymbols": [from_symbol, to_symbol],
            "routeAddresses": [from_address, to_address],
            "source": "v4_closed_loop_db",
            "usableForObservation": True,
        }

    def db_route(pool_steps):
        symbols = [step["routeSymbols"][0] for step in pool_steps] + [pool_steps[-1]["routeSymbols"][1]]
        return {
            "kind": "bridge_closed_loop",
            "closedLoop": True,
            "borrowSymbol": symbols[0],
            "anchorSymbol": symbols[0],
            "bridgeSymbol": symbols[1],
            "targetSymbol": symbols[2],
            "pathSymbols": symbols,
            "pathText": " -> ".join(symbols),
            "routePools": pool_steps,
            "routeSemantics": "x_y_entry_y_z_bridge_z_x_exit",
            "hopCandidates": [],
            "hopCandidateCounts": {},
            "hopCandidateCombinationCount": 1,
            "poolPathKey": "|".join(str(step.get("pool") or "") for step in pool_steps),
            "recommendedFirstPriority": True,
            "routeRecommendationBasis": "fixed_fee_no_hook_low_fee",
            "source": "v4_closed_loop_db",
        }

    route_a = db_route(
        [
            db_step(weth_vlad_a_pool, "WETH", weth, "VLAD", vlad_a),
            db_step(vlad_a_usdg_pool, "VLAD", vlad_a, "USDG", usdg),
            db_step(usdg_weth_pool, "USDG", usdg, "WETH", weth),
        ]
    )
    route_b = db_route(
        [
            db_step(weth_vlad_b_pool, "WETH", weth, "VLAD", vlad_b),
            db_step(vlad_b_usdg_pool, "VLAD", vlad_b, "USDG", usdg),
            db_step(usdg_weth_pool, "USDG", usdg, "WETH", weth),
        ]
    )
    db_observation = {
        "mode": "dex_market_observation",
        "chain_id": 4663,
        "selected_protocols": [],
        "marketObservationSource": "sqlite_db",
        "marketObservationSourceStage": "sqlite_db",
        "tableSource": "ob_table",
        "referencedTable": "ch_table",
        "observationAnchorSymbols": ["WETH", "USDG"],
        "anchorGroups": payload["anchorGroups"],
        "pairs": payload["pairs"],
        "bridgePairs": payload["pairs"],
        "observationSequences": [],
        "closedLoopSequences": [],
        "closedLoopRoutes": [route_a, route_b],
        "chainSpecificObservationPairs": payload["pairs"],
        "chainSpecificObservationPools": [
            usdg_weth_pool,
            weth_vlad_a_pool,
            weth_vlad_b_pool,
            vlad_a_usdg_pool,
            vlad_b_usdg_pool,
        ],
        "chainSpecificObservationTokens": [],
        "summary": {
            "anchorBorrowSymbols": ["WETH", "USDG"],
            "observationAnchorSymbols": ["WETH", "USDG"],
            "closedLoopAnchorSymbols": ["WETH", "USDG"],
            "observationSequenceCount": "2",
            "closedLoopSequenceCount": "2",
            "closedLoopRouteCount": "2",
        },
    }
    monkeypatch.setattr(
        dex_surfaces,
        "_database_first_robinhood_observation_payload",
        lambda chain_id=None, protocols=None, database_url=None: db_observation,
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
                    "sources": [{"protocol": "morpho_blue", "token_symbol": "WETH", "flash_borrowable": True}],
                }
            },
        },
    )
    real_route_context = dex_surfaces._route_framework_context_from_observation

    def route_context_without_mock_active_filter(observation, pool_payload=None, **kwargs):
        summary = dict(observation.get("summary") or {})
        summary["recentSwapFilterApplied"] = False
        summary["liquidityFilterApplied"] = False
        return real_route_context(
            {
                **observation,
                "recentSwapPoolFilter": {},
                "liquidityPoolFilter": {},
                "summary": summary,
            },
            pool_payload,
            **kwargs,
        )

    monkeypatch.setattr(dex_surfaces, "_route_framework_context_from_observation", route_context_without_mock_active_filter)

    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=4663)
    vlad_routes = [route for route in quotes["routes"] if route["symbolPair"] == "WETH -> VLAD -> USDG -> WETH"]

    assert len(vlad_routes) >= 2
    address_lists = {
        tuple(str(address).lower() for address in route.get("routeAddresses") or [])
        for route in vlad_routes
    }
    assert {vlad_a, vlad_b} <= {address for addresses in address_lists for address in addresses}
    assert not any(route["symbolPair"] == "WETH -> VLAD -> WETH" for route in quotes["routes"])


def test_dex_pool_candidates_returns_chain_owned_empty_when_local_json_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(candidate_pool_cache_store, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )

    payload = pool_candidates.dex_pool_candidates_payload(chain_id=137)

    assert payload["poolStaticTable"]["chainId"] == 137
    assert payload["poolStaticTable"]["exists"] is False
    assert payload["poolStaticTable"]["network"] == "polygon"
    assert payload["fallback_reason"] == "dex_pool_candidates_local_json_missing"
    assert payload["pairs"] == []


def test_dex_pool_candidates_returns_empty_for_unregistered_chain(monkeypatch):
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )

    payload = pool_candidates.dex_pool_candidates_payload(chain_id=999999)

    assert payload["poolStaticTable"]["chainId"] == 999999
    assert payload["poolStaticTable"]["exists"] is False
    assert payload["fallback_reason"] == "dex_pool_static_table_not_configured_for_chain"
    assert payload["pairs"] == []


def test_dex_pool_candidates_uses_candidate_pool_cache_and_full_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_pool_cache_store, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )
    monkeypatch.setattr(pool_candidates, "_candidate_pool_source_available", lambda chain_id: True)

    pool = {
        "pool": "0x" + "1" * 40,
        "protocol": "uniswap_v3",
        "dex": "uniswap_v3",
        "fee": "3000",
        "quality": "pass",
        "token0": "0x" + "a" * 40,
        "token1": "0x" + "b" * 40,
        "token0Symbol": "USDC",
        "token1Symbol": "WETH",
        "balances": {"token0": 10, "token1": 10, "tvlUsd": 1000},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    pair_report = {
        "symbolPair": "USDC:WETH",
        "tokenX": pool["token0"],
        "tokenY": pool["token1"],
        "tokenXSymbol": pool["token0Symbol"],
        "tokenYSymbol": pool["token1Symbol"],
        "preferredPools": [pool],
        "strictQualityPools": [pool],
        "executionPools": [pool],
        "rejectedPools": [],
        "allPools": [pool],
        "hasStrictQualityPools": True,
        "hasPreferredPools": True,
        "allPoolCount": "1",
        "strictQualityPoolCount": "1",
        "preferredPoolCount": "1",
        "executionPoolCount": "1",
        "rejectedPoolCount": "0",
        "rejectReasonCounts": {},
        "classification": {"stableSymbols": ["USDC"], "nonStableSymbols": ["WETH"]},
    }
    source_payload = {
        "mode": "dex_pool_candidates",
        "source": "chain_flow",
        "generatedAt": "2026-08-28T00:00:00+00:00",
        "poolStaticTable": {"path": "source.json", "exists": True, "network": "bnb", "chainId": 56},
        "filters": {},
        "summary": {"pairTableCount": "1", "pairsWithStrictQualityPools": "1", "pairsWithPreferredPools": "1"},
        "pairs": [pair_report],
        "anchorGroups": build_anchor_candidate_groups([pair_report]),
    }
    calls = {"count": 0}

    def fake_dispatch_chain_flow(**kwargs):
        import json

        calls["count"] += 1
        return json.loads(json.dumps(source_payload))

    monkeypatch.setattr(pool_candidates, "dispatch_chain_flow", fake_dispatch_chain_flow)

    first = pool_candidates.dex_pool_candidates_payload(chain_id=56)
    cache_paths = candidate_pool_cache_store.candidate_pool_cache_paths(network="bnb", chain_id=56, cache_root=tmp_path)
    second = pool_candidates.dex_pool_candidates_payload(chain_id=56)
    refreshed = pool_candidates.dex_pool_candidates_payload(chain_id=56, refresh=True)
    full = pool_candidates.dex_pool_candidates_payload(chain_id=56, refresh=True, force_full=True)

    assert calls["count"] == 3
    assert cache_paths["snapshot"].exists()
    assert cache_paths["manifest"].exists()
    assert cache_paths["shards"].exists()
    assert (cache_paths["shards"] / "usdc.json").exists()
    assert first["poolStaticTable"]["path"] == str(cache_paths["snapshot"])
    assert second["poolStaticTable"]["path"] == str(cache_paths["snapshot"])
    assert refreshed["poolStaticTable"]["path"] == str(cache_paths["snapshot"])
    assert full["candidatePoolCache"]["forceFull"] is True


def test_dex_pool_candidates_reads_robinhood_candidate_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "robinhood_dex_candidates.json"
    monkeypatch.setattr(pool_candidates, "ROBINHOOD_DEX_MARKET_POOL_PATH", tmp_path / "missing_market.json")
    monkeypatch.setattr(pool_candidates, "ROBINHOOD_DEX_EVIDENCE_ROOT", tmp_path / "missing_evidence")
    _write_json(
        cache_path,
        {
            "schemaVersion": "robinhood-candidate-cache-v1",
            "network": "robinhood",
            "generatedAt": "2026-08-24T00:00:00+00:00",
            "candidates": [
                {
                    "route_key": "USDG:WETH:100->500",
                    "route_symbols": ["USDG", "WETH", "USDG"],
                    "pool_addresses": ["0x" + "a" * 40, "0x" + "b" * 40],
                    "evidence": {
                        "pools": [
                            {"liquidity": "960106731002746306", "blockNumber": 43037858},
                            {"liquidity": "290336060850729630", "blockNumber": 43037858},
                        ],
                    },
                    "status": "observed",
                }
            ],
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {
            "protocols": [{"protocol": "morpho_blue"}, {"protocol": "uniswap_v3"}],
            "removed_protocols": [],
        },
    )

    payload = pool_candidates.dex_pool_candidates_payload(cache_path, protocols=["uniswap_v3"])
    lending_filtered_payload = pool_candidates.dex_pool_candidates_payload(cache_path, protocols=["morpho_blue"])

    assert payload["poolStaticTable"]["chainId"] == 4663
    assert payload["poolStaticTable"]["network"] == "robinhood"
    assert payload["summary"]["pairTableCount"] == "1"
    assert payload["pairs"][0]["symbolPair"] == "USDG:WETH"
    assert payload["pairs"][0]["preferredPoolCount"] == "2"
    assert payload["pairs"][0]["preferredPools"][0]["protocol"] == "uniswap_v3"
    assert payload["pairs"][0]["preferredPools"][0]["fee"] == "100"
    assert payload["pairs"][0]["preferredPools"][0]["liquidity"] == "960106731002746306"
    assert payload["pairs"][0]["preferredPools"][0]["depthBand"]["level"] in {"observed", "liquidity-index"}
    assert lending_filtered_payload["summary"]["pairTableCount"] == "1"
    assert lending_filtered_payload["pairs"][0]["symbolPair"] == "USDG:WETH"


def test_dex_pool_candidates_prefers_robinhood_market_pool_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "robinhood_dex_candidates.json"
    market_path = tmp_path / "robinhood_dex_market_pools.json"
    monkeypatch.setattr(pool_candidates, "ROBINHOOD_DEX_MARKET_POOL_PATH", market_path)
    monkeypatch.setattr(pool_candidates, "ROBINHOOD_DEX_EVIDENCE_ROOT", tmp_path / "missing_evidence")
    _write_json(
        cache_path,
        {
            "schemaVersion": "robinhood-candidate-cache-v1",
            "network": "robinhood",
            "chainId": 4663,
            "generatedAt": "2026-08-24T00:00:00+00:00",
            "candidates": [
                {
                    "route_key": "USDG:WETH:100->500",
                    "route_symbols": ["USDG", "WETH", "USDG"],
                    "pool_addresses": ["0x" + "a" * 40, "0x" + "b" * 40],
                }
            ],
        },
    )
    _write_json(
        market_path,
        {
            "schemaVersion": "robinhood-dex-market-cache-v1",
            "network": "robinhood",
            "chainId": 4663,
            "generatedAt": "2026-08-24T00:00:00+00:00",
            "pools": [
                {
                    "pool": "0x" + "1" * 40,
                    "dex": "uniswap",
                    "protocol": "uniswap_v3",
                    "token0Symbol": "WALLET",
                    "token1Symbol": "WETH",
                    "symbolPair": "WALLET:WETH",
                    "quality": "pass",
                    "tvlUsd": 796394.48,
                    "volumeH24Usd": 517990.57,
                },
                {
                    "pool": "0x" + "2" * 40,
                    "dex": "uniswap",
                    "protocol": "uniswap_v3",
                    "token0Symbol": "BRODIE",
                    "token1Symbol": "WETH",
                    "symbolPair": "BRODIE:WETH",
                    "quality": "pass",
                    "tvlUsd": 190013.68,
                    "volumeH24Usd": 131934.42,
                },
            ],
        },
    )

    payload = pool_candidates.dex_pool_candidates_payload(cache_path, protocols=["morpho_blue"])

    assert payload["filters"]["source"] == "robinhood_dexscreener_market_cache"
    assert payload["summary"]["pairTableCount"] == "2"
    assert {pair["symbolPair"] for pair in payload["pairs"]} == {"WALLET:WETH", "BRODIE:WETH"}
    by_pair = {pair["symbolPair"]: pair for pair in payload["pairs"]}
    assert by_pair["WALLET:WETH"]["preferredPools"][0]["balances"]["tvlUsd"] == 796394.48
