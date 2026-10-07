from __future__ import annotations


def _pool(pool_id: str, *, quote_summary: dict | None = None, tvl_usd: float = 100.0) -> dict:
    return {
        "pool": pool_id,
        "token0Symbol": "A",
        "token1Symbol": "B",
        "balances": {"tvlUsd": tvl_usd},
        "hasLiquidity": True,
        "usableForObservation": True,
        "quoteSummary": {"outputAmount": 10.0} if quote_summary is None else quote_summary,
    }


def _candidate(leg: str, pools: list[dict], *, preferred_pool_id: str | None = None) -> dict:
    return {
        "leg": leg,
        "direction": "yc" if leg == "x-y" else "bridge" if leg == "y-z" else "zc",
        "symbolPair": f"{leg} pair",
        "poolCount": len(pools),
        "usablePoolCount": len(pools),
        "selectionBasis": "tvl_depth_desc",
        "preferredPoolId": preferred_pool_id or pools[0]["pool"],
        "pools": pools,
    }


def _route(*, pool_ids: tuple[str, str, str] = ("a", "b", "c")) -> dict:
    x_y = [
        _pool(pool_ids[0], tvl_usd=200.0),
        _pool("x_y_shallow", tvl_usd=100.0),
        _pool("x_y_shallow_2", tvl_usd=90.0),
    ]
    y_z = [
        _pool(pool_ids[1], tvl_usd=300.0),
        _pool("y_z_shallow", tvl_usd=100.0),
    ]
    z_x = [
        _pool(pool_ids[2], tvl_usd=400.0),
        _pool("z_x_shallow", tvl_usd=100.0),
        _pool("z_x_shallow_2", tvl_usd=90.0),
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


def test_replacement_route_variants_are_bounded_and_only_change_default_legs():
    from market.binance_market.dex_route_candidate_selection import replacement_route_variants

    variants = replacement_route_variants(_route())

    assert len(variants) == 4
    for variant in variants:
        replacement = variant["hopCandidateReplacement"]
        assert set(replacement["changedLegs"]) <= {"x-y", "z-x"}
        assert replacement["temporaryRoute"] is True
        assert replacement["notForDefaultScan"] is True
        assert replacement["executeLast"] is True
        assert variant["poolPathKey"]
    assert variant["referencePoolPathKey"] == "a|b|c"


def test_replacement_route_variants_without_price_evidence_keep_liquid_candidates():
    from market.binance_market.dex_route_candidate_selection import replacement_route_variants

    route = _route()
    for candidate in route["hopCandidates"]:
        for pool in candidate["pools"]:
            pool["quoteSummary"] = {}
            pool["balances"]["tvlUsd"] = 1_000_000.0

    variants = replacement_route_variants(route)

    assert variants
    assert all(variant["temporaryRoute"] for variant in variants)


def test_maybe_replace_route_with_better_candidates_keeps_default_when_no_better():
    from market.binance_market.dex_route_candidate_selection import maybe_replace_route_with_better_candidates

    route = _route()
    pools = list(route["routePools"])
    quote_analysis = {
        "complete": True,
        "profitable": False,
        "referenceNetProfitUsd": -1.0,
        "steps": [],
    }

    replaced_route, replaced_pools, analysis, size_analysis, replacement = maybe_replace_route_with_better_candidates(
        route=route,
        pools=pools,
        quote_analysis=quote_analysis,
        route_quote_analysis_fn=lambda route, price_map, chain_id: {
            "complete": True,
            "profitable": False,
            "referenceNetProfitUsd": -1.0,
        },
        price_map={},
        chain_id=None,
        route_size_analysis_fn=lambda route, price_map, chain_id: {"recommendedBorrowInputAmount": 1.0},
    )

    assert replaced_route["poolPathKey"] == "a|b|c"
    assert replaced_pools == pools
    assert replacement["attempted"] is True
    assert replacement["reason"] == "no_better_candidate"
    assert replacement["variantsEvaluated"] == 4


def test_maybe_replace_route_with_better_candidates_chooses_higher_profit_variant():
    from market.binance_market.dex_route_candidate_selection import maybe_replace_route_with_better_candidates

    route = _route()
    quote_analysis = {
        "complete": True,
        "profitable": False,
        "referenceNetProfitUsd": -1.0,
        "steps": [],
    }

    def route_quote_analysis_fn(variant, *, price_map, chain_id):
        return {
            "complete": True,
            "profitable": True,
            "referenceNetProfitUsd": 1.0,
        }

    def route_size_analysis_fn(variant, *, price_map, chain_id):
        return {"recommendedBorrowInputAmount": 1.0}

    replaced_route, replaced_pools, analysis, size_analysis, replacement = maybe_replace_route_with_better_candidates(
        route=route,
        pools=list(route["routePools"]),
        quote_analysis=quote_analysis,
        route_quote_analysis_fn=route_quote_analysis_fn,
        price_map={},
        chain_id=None,
        route_size_analysis_fn=route_size_analysis_fn,
    )

    assert replacement["reason"] == "better_candidate_found"
    assert replacement["temporaryRoute"] is True
    assert replacement["notForDefaultScan"] is True
    assert replacement["historyOnlyAfterExecution"] is True
    assert replaced_route["temporaryRoute"] is True
    assert replaced_route["temporaryRoutePolicy"]["referencePoolPathKey"] == "a|b|c"
    assert replaced_route["referencePoolPathKey"] == "a|b|c"
    assert replaced_route["poolPathKey"] != "a|b|c"


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
