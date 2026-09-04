import pytest

from core.market_observation_store import load_market_observation_static, write_market_observation_static


def test_observation_static_cache_persists_and_checks_scope(tmp_path):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    write_market_observation_static(
        {"summary": {"count": 1}}, network="bnb", chain_id=56, protocols=["venus"], root=tmp_path,
        expected_scope=scope,
    )
    loaded = load_market_observation_static(
        network="bnb", chain_id=56, protocols=["venus"], root=tmp_path, expected_scope=scope,
    )
    assert loaded["scope"] == scope
    with pytest.raises(ValueError, match="scope mismatch on chain_id"):
        load_market_observation_static(
            network="bnb", chain_id=56, protocols=["venus"], root=tmp_path,
            expected_scope={**scope, "chain_id": 1},
        )
