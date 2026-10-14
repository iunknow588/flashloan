from __future__ import annotations


def test_robinhood_overview_without_closed_loop_routes_never_recomputes(monkeypatch):
    import market.binance_market.dex_surface_observation_impl as impl

    def fail_recompute(*args, **kwargs):
        raise AssertionError("Robinhood DB-first overview must not recompute observation sequences")

    monkeypatch.setattr(impl, "_anchor_observation_sequences", fail_recompute)
    observation = {
        "candidateObservationGroups": [],
        "anchorGroups": [],
        "pairs": [],
        "bridgePairs": [],
        "marketObservationSource": "sqlite_db",
        "closedLoopRoutes": [],
        "chainSpecificObservationPairs": [],
    }
    candidates, watch_pairs, anchors = impl._overview_path_candidates_from_observation_sets(
        observation,
        chain_id=4663,
        protocols=["uniswap_v4"],
    )

    assert candidates == []
    assert watch_pairs == []
    assert anchors == []


def test_chain_db_first_only_scope_is_robinhood_v4_only():
    import market.binance_market.dex_surface_observation_impl as impl

    assert impl._chain_db_first_only(4663) is True
    assert impl._chain_db_first_only(4663, protocols=["uniswap_v4"]) is True
    assert impl._chain_db_first_only(46630, protocols=["uniswap_v4"]) is True
    assert impl._chain_db_first_only(46630, protocols=["uniswap_v3"]) is False
    assert impl._chain_db_first_only(56) is False
    assert impl._chain_db_first_only(None) is False


def test_observation_surface_db_first_only_scope_stays_in_sync():
    from market.binance_market.dex_observation_surface import _chain_db_first_only

    assert _chain_db_first_only(4663) is True
    assert _chain_db_first_only(4663, protocols=["uniswap_v4"]) is True
    assert _chain_db_first_only(46630, protocols=["uniswap_v4"]) is True
    assert _chain_db_first_only(46630, protocols=["uniswap_v3"]) is False
    assert _chain_db_first_only(56) is False
    assert _chain_db_first_only(None) is False
