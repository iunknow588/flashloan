import pytest

from market.binance_market.candidate_pool_cache.store import load_candidate_pool_snapshot, refresh_candidate_pool_cache


def test_candidate_pool_cache_persists_and_checks_scope(tmp_path):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    snapshot = refresh_candidate_pool_cache(
        {"candidatePoolCache": {"groups": []}},
        network="bnb", chain_id=56, cache_root=tmp_path, force_full=True,
        expected_scope=scope,
    )
    assert snapshot["scope"] == scope
    assert load_candidate_pool_snapshot(network="bnb", chain_id=56, cache_root=tmp_path, expected_scope=scope)["scope"] == scope
    with pytest.raises(ValueError, match="scope mismatch on chain_id"):
        load_candidate_pool_snapshot(network="bnb", chain_id=56, cache_root=tmp_path, expected_scope={**scope, "chain_id": 1})
