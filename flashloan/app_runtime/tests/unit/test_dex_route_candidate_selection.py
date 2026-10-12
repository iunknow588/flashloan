from __future__ import annotations

import pytest


def _pool(
    pool_id: str,
    *,
    quote_summary: dict | None = None,
    tvl_usd: float = 100.0,
    token0_symbol: str = "A",
    token1_symbol: str = "B",
    market_price: float | None = None,
) -> dict:
    payload = {
        "pool": pool_id,
        "token0Symbol": token0_symbol,
        "token1Symbol": token1_symbol,
        "balances": {"tvlUsd": tvl_usd},
        "hasLiquidity": True,
        "usableForObservation": True,
        "quoteSummary": {"outputAmount": 10.0} if quote_summary is None else quote_summary,
    }
    if market_price is not None:
        payload["market"] = {"priceNative": market_price}
    return payload


def _candidate(leg: str, pools: list[dict], *, preferred_pool_id: str | None = None) -> dict:
    leg_tokens = {
        "x-y": ("A", "B", "0xA", "0xB"),
        "y-z": ("B", "C", "0xB", "0xC"),
        "z-x": ("C", "A", "0xC", "0xA"),
    }
    from_symbol, to_symbol, from_address, to_address = leg_tokens.get(leg, ("", "", "", ""))
    return {
        "leg": leg,
        "direction": "yc" if leg == "x-y" else "bridge" if leg == "y-z" else "zc",
        "symbolPair": f"{leg} pair",
        "fromSymbol": from_symbol,
        "toSymbol": to_symbol,
        "fromAddress": from_address,
        "toAddress": to_address,
        "poolCount": len(pools),
        "usablePoolCount": len(pools),
        "selectionBasis": "tvl_depth_desc",
        "preferredPoolId": preferred_pool_id or pools[0]["pool"],
        "pools": pools,
    }


def _route(*, pool_ids: tuple[str, str, str] = ("a", "b", "c")) -> dict:
    x_y = [
        _pool(pool_ids[0], tvl_usd=200.0, token0_symbol="A", token1_symbol="B", market_price=1.0),
        _pool("x_y_shallow", tvl_usd=100.0, token0_symbol="A", token1_symbol="B", market_price=1.0),
        _pool("x_y_shallow_2", tvl_usd=90.0, token0_symbol="A", token1_symbol="B", market_price=1.0),
    ]
    y_z = [
        _pool(pool_ids[1], tvl_usd=300.0, token0_symbol="B", token1_symbol="C", market_price=1.0),
        _pool("y_z_shallow", tvl_usd=100.0, token0_symbol="B", token1_symbol="C", market_price=1.0),
    ]
    z_x = [
        _pool(pool_ids[2], tvl_usd=400.0, token0_symbol="C", token1_symbol="A", market_price=1.0),
        _pool("z_x_shallow", tvl_usd=100.0, token0_symbol="C", token1_symbol="A", market_price=1.0),
        _pool("z_x_shallow_2", tvl_usd=90.0, token0_symbol="C", token1_symbol="A", market_price=1.0),
    ]
    hop_candidates = [
        _candidate("x-y", x_y, preferred_pool_id=pool_ids[0]),
        _candidate("y-z", y_z, preferred_pool_id=pool_ids[1]),
        _candidate("z-x", z_x, preferred_pool_id=pool_ids[2]),
    ]
    return {
        "symbolPair": "A -> B -> C -> A",
        "routeSymbols": ["A", "B", "C", "A"],
        "routeAddresses": ["0xA", "0xB", "0xC", "0xA"],
        "routePools": [x_y[0], y_z[0], z_x[0]],
        "poolPathKey": "|".join(pool_ids),
        "hopCandidates": hop_candidates,
        "hopCandidateCounts": {"x-y": 2, "y-z": 2, "z-x": 2},
        "hopCandidateCombinationCount": 8,
        "recommendedFirstPriority": True,
        "routeRecommendationBasis": "tvl_depth_desc",
    }


def test_output_leader_pool_ids_prefers_best_quote_output_over_tvl():
    from market.binance_market.dex_route_candidate_selection import output_leader_pool_ids

    route = _route()
    route["hopCandidates"][0]["pools"] = [
        _pool("deep_low_output", tvl_usd=1000.0, quote_summary={"outputAmount": 1.0}),
        _pool("shallow_high_output", tvl_usd=100.0, quote_summary={"outputAmount": 2.0}),
    ]
    route["hopCandidates"][2]["pools"] = [
        _pool("exit_high_output", tvl_usd=100.0, quote_summary={"finalOutputUsd": 3.0}),
        _pool("exit_deep", tvl_usd=1000.0, quote_summary={"finalOutputUsd": 2.0}),
    ]

    selected = output_leader_pool_ids(route)

    assert selected["x-y"] == "shallow_high_output"
    assert selected["z-x"] == "exit_high_output"
    assert "y-z" not in selected


def test_output_leader_pool_ids_skips_pools_without_liquidity():
    from market.binance_market.dex_route_candidate_selection import output_leader_pool_ids

    route = _route()
    no_liquidity_high_price = _pool(
        "no_liquidity_high_price",
        quote_summary={"outputAmount": 99.0},
    )
    no_liquidity_high_price["hasLiquidity"] = False
    no_liquidity_high_price["usableForObservation"] = False
    liquid_lower_price = _pool(
        "liquid_lower_price",
        quote_summary={"outputAmount": 2.0},
    )
    route["hopCandidates"][0]["pools"] = [
        no_liquidity_high_price,
        liquid_lower_price,
    ]

    selected = output_leader_pool_ids(route)

    assert selected["x-y"] == "liquid_lower_price"


def test_output_leader_pool_ids_never_falls_back_to_depth():
    from market.binance_market.dex_route_candidate_selection import output_leader_pool_ids

    route = _route()
    for candidate in route["hopCandidates"]:
        for pool in candidate["pools"]:
            pool["quoteSummary"] = {}
            pool["balances"]["tvlUsd"] = 1_000_000.0

    selected = output_leader_pool_ids(route)

    assert selected == {}


def _settlement(*, supported=True, pairs=None) -> dict:
    profile = {
        "supported": supported,
        "reactor_family": "Dutch V3 Reactor",
        "chain_id": 4663,
    }
    if pairs is not None:
        profile["availablePairs"] = pairs
    return {"uniswapx": profile}


def _uniswapx_analysis(*, return_bps: float = 120.0) -> dict:
    return {
        "complete": True,
        "profitable": return_bps >= 100,
        "notionalUsd": 1000.0,
        "referenceNetProfitUsd": 1000.0 * return_bps / 10_000.0,
        "referenceNetReturnBps": return_bps,
        "steps": [],
    }


def test_maybe_replace_route_with_better_candidates_skips_profitable_route():
    from market.binance_market.dex_route_candidate_selection import maybe_replace_route_with_better_candidates

    route = _route()
    quote_analysis = {
        "complete": True,
        "profitable": True,
        "referenceNetProfitUsd": 5.0,
        "steps": [],
    }

    _, _, _, _, replacement = maybe_replace_route_with_better_candidates(
        route=route,
        pools=list(route["routePools"]),
        quote_analysis=quote_analysis,
        route_quote_analysis_fn=lambda route, price_map, chain_id: quote_analysis,
        price_map={},
        chain_id=4663,
        settlement=_settlement(),
    )

    assert replacement["attempted"] is False
    assert replacement["reason"] == "no_replacement_trigger"


def test_maybe_replace_route_with_better_candidates_fails_fast_when_uniswapx_unsupported():
    from market.binance_market.dex_route_candidate_selection import maybe_replace_route_with_better_candidates

    route = _route()
    calls = {"quote": 0}

    def route_quote_analysis_fn(route, *, price_map, chain_id):
        calls["quote"] += 1
        return _uniswapx_analysis()

    replaced_route, _, analysis, _, replacement = maybe_replace_route_with_better_candidates(
        route=route,
        pools=list(route["routePools"]),
        quote_analysis={
            "complete": True,
            "profitable": False,
            "referenceNetProfitUsd": -1.0,
            "steps": [],
        },
        route_quote_analysis_fn=route_quote_analysis_fn,
        price_map={},
        chain_id=4663,
        settlement=_settlement(supported=False),
    )

    assert calls["quote"] == 0
    assert replacement["attempted"] is True
    assert replacement["reason"] == "uniswapx_unsupported"
    assert replacement["stopSearching"] is True
    assert replaced_route["uniswapxFallback"]["status"] == "failed"
    assert analysis["terminalStatus"] == "uniswapx_fallback_failed"
    assert replaced_route["poolPathKey"] == "a|b|c"


def test_maybe_replace_route_with_better_candidates_fails_when_middle_hop_missing():
    from market.binance_market.dex_route_candidate_selection import maybe_replace_route_with_better_candidates

    route = _route()
    route["hopCandidates"] = [candidate for candidate in route["hopCandidates"] if candidate["leg"] != "y-z"]
    calls = {"quote": 0}

    def route_quote_analysis_fn(route, *, price_map, chain_id):
        calls["quote"] += 1
        return _uniswapx_analysis()

    _, _, _, _, replacement = maybe_replace_route_with_better_candidates(
        route=route,
        pools=list(route["routePools"]),
        quote_analysis={
            "complete": True,
            "profitable": False,
            "referenceNetProfitUsd": -1.0,
            "steps": [],
        },
        route_quote_analysis_fn=route_quote_analysis_fn,
        price_map={},
        chain_id=4663,
        settlement=_settlement(),
    )

    assert calls["quote"] == 0
    assert replacement["reason"] == "uniswapx_middle_hop_missing"
    assert replacement["stopSearching"] is True


def test_maybe_replace_route_with_better_candidates_fails_when_pair_unavailable():
    from market.binance_market.dex_route_candidate_selection import maybe_replace_route_with_better_candidates

    route = _route()

    _, _, _, _, replacement = maybe_replace_route_with_better_candidates(
        route=route,
        pools=list(route["routePools"]),
        quote_analysis={
            "complete": True,
            "profitable": False,
            "referenceNetProfitUsd": -1.0,
            "steps": [],
        },
        route_quote_analysis_fn=lambda route, price_map, chain_id: _uniswapx_analysis(),
        price_map={},
        chain_id=4663,
        settlement=_settlement(pairs=[]),
    )

    assert replacement["reason"] == "uniswapx_middle_hop_unavailable"
    assert replacement["stopSearching"] is True


def test_maybe_replace_route_with_better_candidates_fails_when_parameters_unavailable():
    from market.binance_market.dex_route_candidate_selection import maybe_replace_route_with_better_candidates

    route = _route()
    for candidate in route["hopCandidates"]:
        for pool in candidate["pools"]:
            pool.pop("market", None)
    for pool in route["routePools"]:
        pool.pop("market", None)
    calls = {"quote": 0}

    def route_quote_analysis_fn(route, *, price_map, chain_id):
        calls["quote"] += 1
        return _uniswapx_analysis()

    _, _, _, _, replacement = maybe_replace_route_with_better_candidates(
        route=route,
        pools=list(route["routePools"]),
        quote_analysis={
            "complete": True,
            "profitable": False,
            "referenceNetProfitUsd": -1.0,
            "steps": [],
        },
        route_quote_analysis_fn=route_quote_analysis_fn,
        price_map={},
        chain_id=4663,
        settlement=_settlement(),
    )

    assert calls["quote"] == 0
    assert replacement["reason"] == "uniswapx_middle_hop_parameters_unavailable"
    assert replacement["stopSearching"] is True


def test_maybe_replace_route_with_better_candidates_uses_uniswapx_middle_hop_only():
    from market.binance_market.dex_route_candidate_selection import maybe_replace_route_with_better_candidates

    route = _route()
    pools = list(route["routePools"])
    calls = {"quote": 0, "size": 0}

    def route_quote_analysis_fn(route, *, price_map, chain_id):
        calls["quote"] += 1
        assert route["hopCandidates"][1]["venue"] == "uniswapx"
        return _uniswapx_analysis(return_bps=120.0)

    def route_size_analysis_fn(route, *, price_map, chain_id):
        calls["size"] += 1
        return {"recommendedBorrowInputAmount": 1.0}

    replaced_route, replaced_pools, analysis, size_analysis, replacement = maybe_replace_route_with_better_candidates(
        route=route,
        pools=pools,
        quote_analysis={
            "complete": True,
            "profitable": False,
            "referenceNetProfitUsd": -1.0,
            "steps": [],
        },
        route_quote_analysis_fn=route_quote_analysis_fn,
        price_map={},
        chain_id=4663,
        route_size_analysis_fn=route_size_analysis_fn,
        settlement=_settlement(),
    )

    assert calls["quote"] == 1
    assert calls["size"] == 1
    assert replacement["reason"] == "uniswapx_middle_hop_fallback"
    assert replacement["changedLegs"] == ["y-z"]
    assert replacement["middleHop"]["symbolPair"] == "B -> C"
    assert (replaced_route["hopCandidates"][0].get("venue") or "") != "uniswapx"
    assert replaced_route["hopCandidates"][1]["venue"] == "uniswapx"
    assert (replaced_route["hopCandidates"][2].get("venue") or "") != "uniswapx"
    assert replaced_route["uniswapxFallback"]["status"] == "selected"
    assert replaced_route["temporaryRoute"] is True
    assert replaced_route["referencePoolPathKey"] == "a|b|c"
    assert replaced_pools == pools
    assert replaced_route["routePools"] == pools
    assert analysis["uniswapxFallbackSelected"] is True
    assert analysis["uniswapxMinProfitBps"] == 100
    assert size_analysis["recommendedBorrowInputAmount"] == 1.0
    fallback_params = replaced_route["uniswapxFallback"]["quoteParameters"]
    assert fallback_params["sellToken"] == "B"
    assert fallback_params["buyToken"] == "C"
    assert fallback_params["sellAmount"] == pytest.approx(fallback_params["firstHopOutputAmount"])
    assert fallback_params["minBuyAmount"] > fallback_params["sellAmount"]
    assert fallback_params["targetProfitBps"] == 100
    quote_request = replaced_route["quoteInputs"]["quoteRequests"][-1]
    assert quote_request["venue"] == "uniswapx"
    assert quote_request["amount"] == pytest.approx(fallback_params["sellAmount"])
    assert quote_request["minBuyAmount"] == pytest.approx(fallback_params["minBuyAmount"])


def test_maybe_replace_route_with_better_candidates_rejects_below_minimum_profit():
    from market.binance_market.dex_route_candidate_selection import maybe_replace_route_with_better_candidates

    route = _route()

    _, _, analysis, _, replacement = maybe_replace_route_with_better_candidates(
        route=route,
        pools=list(route["routePools"]),
        quote_analysis={
            "complete": True,
            "profitable": False,
            "referenceNetProfitUsd": -1.0,
            "steps": [],
        },
        route_quote_analysis_fn=lambda route, price_map, chain_id: _uniswapx_analysis(return_bps=50.0),
        price_map={},
        chain_id=4663,
        settlement=_settlement(),
    )

    assert replacement["reason"] == "uniswapx_fallback_profit_below_minimum"
    assert replacement["stopSearching"] is True
    assert analysis["terminalStatus"] == "uniswapx_fallback_failed"


def test_maybe_replace_route_with_better_candidates_skips_uncomplete_analysis():
    from market.binance_market.dex_route_candidate_selection import maybe_replace_route_with_better_candidates

    route = _route()
    quote_analysis = {
        "complete": False,
        "profitable": False,
        "referenceNetProfitUsd": -1.0,
        "steps": [],
    }

    _, _, _, _, replacement = maybe_replace_route_with_better_candidates(
        route=route,
        pools=list(route["routePools"]),
        quote_analysis=quote_analysis,
        route_quote_analysis_fn=lambda route, price_map, chain_id: quote_analysis,
        price_map={},
        chain_id=None,
    )

    assert replacement["attempted"] is False
    assert replacement["reason"] == "no_replacement_trigger"


def test_maybe_replace_route_with_better_candidates_skips_non_recommended_route():
    from market.binance_market.dex_route_candidate_selection import maybe_replace_route_with_better_candidates

    route = _route()
    route["recommendedFirstPriority"] = False
    quote_analysis = {
        "complete": True,
        "profitable": False,
        "referenceNetProfitUsd": -1.0,
        "steps": [],
    }

    _, _, _, _, replacement = maybe_replace_route_with_better_candidates(
        route=route,
        pools=list(route["routePools"]),
        quote_analysis=quote_analysis,
        route_quote_analysis_fn=lambda route, price_map, chain_id: quote_analysis,
        price_map={},
        chain_id=None,
    )

    assert replacement["attempted"] is False
    assert replacement["reason"] == "no_replacement_trigger"
