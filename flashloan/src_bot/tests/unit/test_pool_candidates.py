from market.binance_market import quote_service
from market.binance_market import pool_candidates
from market.binance_market import dex_surfaces
from strategy.arbitrage import ArbitrageConfig


def _write_json(path, payload):
    import json

    path.write_text(json.dumps(payload), encoding="utf-8")


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
                    "symbolPair": "USDC:USDT",
                    "dex": "pancakeswap_v3",
                    "protocol": "v3",
                    "token0Symbol": "USDC",
                    "token1Symbol": "USDT",
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

    candidates = pool_candidates.dex_pool_candidates_payload(cache_path)
    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=56)
    request = dex_surfaces.dex_quote_request_payload(chain_id=56, pair="USDC:USDT", venue="uniswapx", amount="1000")

    assert candidates["pairs"][0]["preferredPoolCount"] == "1"
    assert candidates["pairs"][0]["flashLoanLinkage"]["ready_for_atomic_arbitrage"] is False
    assert quotes["summary"]["routeCount"] == 1
    assert quotes["summary"]["uniswapxQuoteableRouteCount"] == 1
    assert quotes["routes"][0]["quoteInputs"]["quoteRequests"][0]["venue"] == "amm_pool"
    assert any(item["venue"] == "uniswapx" for item in quotes["routes"][0]["quoteInputs"]["quoteRequests"])
    assert request["ok"] is True
    assert request["status"] == "quote_request_ready"
    assert request["quoteRequests"][0]["venue"] == "uniswapx"


def test_dex_pool_candidates_returns_empty_for_unconfigured_chain(monkeypatch):
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )

    payload = pool_candidates.dex_pool_candidates_payload(chain_id=137)

    assert payload["poolStaticTable"]["chainId"] == 137
    assert payload["poolStaticTable"]["exists"] is False
    assert payload["fallback_reason"] == "dex_pool_static_table_not_configured_for_chain"
    assert payload["pairs"] == []


def test_dex_pool_candidates_reads_robinhood_candidate_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "robinhood_dex_candidates.json"
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
    assert payload["pairs"][0]["preferredPools"][0]["depthBand"]["level"] == "observed"
    assert lending_filtered_payload["summary"]["pairTableCount"] == "1"
    assert lending_filtered_payload["pairs"][0]["symbolPair"] == "USDG:WETH"
