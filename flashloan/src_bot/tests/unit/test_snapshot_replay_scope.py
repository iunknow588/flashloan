import pytest

from core.snapshot_replay import build_workflow_replay_fixture


def test_workflow_replay_rejects_incomplete_scope_contract():
    stage = {
        "scope": {"market_id": "m", "chain_id": 56, "network": "bnb"},
        "generation_id": "g1",
    }

    with pytest.raises(ValueError, match="replay scope invalid.*protocol"):
        build_workflow_replay_fixture(
            observation=stage, overview=dict(stage), quote=dict(stage), gate_allowed=False
        )


def test_workflow_replay_normalizes_numeric_chain_id():
    stage = {
        "scope": {"market_id": "m", "chain_id": "56", "network": "BNB", "protocol": "VENUS"},
        "generation_id": "g1",
    }

    fixture = build_workflow_replay_fixture(
        observation=stage, overview=dict(stage), quote=dict(stage), gate_allowed=False
    )

    assert fixture["replay"]["target_hash"].startswith("sha256:")
    assert fixture["scope"] == {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}


def test_workflow_replay_fixture_rejects_top_level_scope_drift(tmp_path):
    from core.snapshot_replay import validate_workflow_replay_fixture

    stage = {
        "scope": {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"},
        "generation_id": "g1",
    }
    fixture = build_workflow_replay_fixture(
        observation=stage, overview=dict(stage), quote=dict(stage), gate_allowed=False
    )
    fixture["scope"] = {**fixture["scope"], "protocol": "aave"}
    with pytest.raises(ValueError, match="replay fixture scope mismatch"):
        validate_workflow_replay_fixture(fixture)
from core.snapshot_replay import build_workflow_replay_fixture, snapshot_target_hash


def test_observation_overview_quote_replay_has_stable_target_hash():
    base = {
        "scope": {"market_id": "bnb-dex-arbitrage", "chain_id": 56, "network": "bnb", "protocol": "uniswap_v3"},
        "generation_id": "generation-test-1",
    }
    fixture = build_workflow_replay_fixture(
        observation={**base, "rows": [{"symbol": "AAA"}]},
        overview={**base, "routes": [{"symbolPair": "AAA:USDC"}]},
        quote={**base, "quote": {"requiresLiveQuoter": True}},
        gate_allowed=False,
        reasons=["requires_live_quoter"],
    )
    assert fixture["replay"]["target_hash"] == snapshot_target_hash(
        {"generation_id": fixture["generation_id"], "scope": fixture["scope"], "stages": fixture["stages"]}
    )
    assert fixture["replay"]["gate_allowed"] is False


def test_observation_overview_quote_replay_rejects_scope_or_generation_drift():
    base = {
        "scope": {"market_id": "bnb-dex-arbitrage", "chain_id": 56, "network": "bnb", "protocol": "uniswap_v3"},
        "generation_id": "generation-test-1",
    }
    import pytest

    with pytest.raises(ValueError, match="scope mismatch"):
        build_workflow_replay_fixture(
            observation=base,
            overview={**base, "scope": {**base["scope"], "chain_id": 1}},
            quote=base,
            gate_allowed=False,
        )


def test_workflow_replay_fixture_persists_and_reads_back_with_stable_hash(tmp_path):
    from core.snapshot_replay import read_workflow_replay_fixture, write_workflow_replay_fixture

    base = {
        "scope": {"market_id": "persist-replay", "chain_id": 56, "network": "bnb", "protocol": "uniswap_v3"},
        "generation_id": "generation-persist-1",
    }
    fixture = build_workflow_replay_fixture(
        observation={**base, "rows": [{"symbol": "AAA"}]},
        overview={**base, "routes": [{"symbolPair": "AAA:USDC"}]},
        quote={**base, "quote": {"requiresLiveQuoter": True}},
        gate_allowed=False,
        reasons=["requires_live_quoter"],
    )
    path = tmp_path / "replay" / "fixture.json"

    written = write_workflow_replay_fixture(path, fixture)
    loaded = read_workflow_replay_fixture(written)

    assert loaded["replay"]["target_hash"] == fixture["replay"]["target_hash"]
    assert loaded["replay"]["gate_allowed"] is False
