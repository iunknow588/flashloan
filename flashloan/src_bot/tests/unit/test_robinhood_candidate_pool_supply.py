from dex.intent_trade.chains.robinhood.candidate_pool_supply import summarize_robinhood_candidate_pool_supply


def _pool(symbol0, symbol1, *, protocol="uniswap_v3", tvl=10_000, volume=100, pool=None):
    return {
        "quality": "pass",
        "status": "observable",
        "protocol": protocol,
        "pool": pool or f"0x{symbol0.lower()[:2]}{symbol1.lower()[:2]}".ljust(42, "0"),
        "token0Symbol": symbol0,
        "token1Symbol": symbol1,
        "symbolPair": f"{symbol0}:{symbol1}",
        "tvlUsd": tvl,
        "volumeH24Usd": volume,
    }


def test_candidate_pool_supply_marks_monitoring_and_route_search_ready_without_execution():
    market_cache = {
        "network": "robinhood",
        "chainId": 4663,
        "pools": [
            _pool("WETH", "USDG", pool="0x1000000000000000000000000000000000000001"),
            _pool("WETH", "AAA", pool="0x1000000000000000000000000000000000000002"),
            _pool("USDG", "AAA", pool="0x1000000000000000000000000000000000000003"),
            _pool("WETH", "BBB", pool="0x1000000000000000000000000000000000000004"),
            _pool("USDG", "BBB", pool="0x1000000000000000000000000000000000000005"),
            _pool("WETH", "CCC", pool="0x1000000000000000000000000000000000000006"),
            _pool("USDG", "CCC", pool="0x1000000000000000000000000000000000000007"),
            _pool("WETH", "DDD", pool="0x1000000000000000000000000000000000000008"),
            _pool("USDG", "DDD", pool="0x1000000000000000000000000000000000000009"),
        ],
    }
    static_table = {
        "pools": [{"status": "observable"} for _ in range(4)],
        "candidates": [{"route_kind": "two_pool_same_pair"} for _ in range(2)],
    }

    report = summarize_robinhood_candidate_pool_supply(
        market_cache=market_cache,
        static_table=static_table,
        thresholds={
            "minMarketPools": 5,
            "minLiquidPools": 5,
            "minObservablePairs": 5,
            "minStaticCandidates": 10,
            "minWethDegree": 4,
            "minUsdgDegree": 4,
            "minBridgeTriangles": 1,
            "minMultiPoolPairs": 1,
        },
    )

    assert report["sufficientForMonitoring"] is True
    assert report["sufficientForRouteSearch"] is True
    assert report["sufficientForExecution"] is False
    assert report["executionEnabled"] is False
    assert report["broadcastEnabled"] is False
    assert report["market"]["bridgeTriangleCount"] == 4


def test_candidate_pool_supply_blocks_when_market_pool_supply_is_thin():
    report = summarize_robinhood_candidate_pool_supply(
        market_cache={"network": "robinhood", "chainId": 4663, "pools": [_pool("WETH", "USDG")]},
        static_table={},
        thresholds={
            "minMarketPools": 5,
            "minLiquidPools": 5,
            "minObservablePairs": 5,
            "minStaticCandidates": 10,
            "minWethDegree": 4,
            "minUsdgDegree": 4,
            "minBridgeTriangles": 1,
            "minMultiPoolPairs": 1,
        },
    )

    assert report["sufficientForMonitoring"] is False
    assert report["sufficientForRouteSearch"] is False
    assert "marketPoolsEnough" in report["blockingReasons"]
    assert "liquidPoolsEnough" in report["blockingReasons"]


def test_candidate_pool_supply_rejects_robinhood_testnet_scope():
    report = summarize_robinhood_candidate_pool_supply(
        market_cache={"network": "robinhood-testnet", "chainId": 46630, "pools": [_pool("WETH", "USDG")]},
        static_table={},
    )

    assert report["scope"]["expectedChainId"] == 4663
    assert report["scope"]["actualChainId"] == 46630
    assert report["sufficientForMonitoring"] is False
    assert report["sufficientForRouteSearch"] is False
    assert report["sufficientForExecution"] is False
    assert report["blockingReasons"] == ["robinhood_mainnet_scope_required"]
