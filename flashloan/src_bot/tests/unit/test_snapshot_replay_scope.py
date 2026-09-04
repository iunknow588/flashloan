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
