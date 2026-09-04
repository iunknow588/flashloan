import pytest

from core.snapshot_replay import (
    build_snapshot_replay_result,
    build_workflow_replay_fixture,
    canonical_snapshot,
    snapshot_target_hash,
    read_workflow_replay_fixture,
    write_workflow_replay_fixture,
    verify_snapshot_replay,
)


def test_snapshot_hash_is_stable_across_mapping_order_and_runtime_timestamps():
    first = {"scope": {"chain_id": 56, "market_id": "m"}, "amount": 10, "observed_at": "one"}
    second = {"observed_at": "two", "amount": 10, "scope": {"market_id": "m", "chain_id": 56}}
    assert snapshot_target_hash(first) == snapshot_target_hash(second)


def test_snapshot_hash_changes_when_business_input_changes():
    base = {"scope": {"chain_id": 56, "market_id": "m"}, "amount": 10}
    changed = {"scope": {"chain_id": 56, "market_id": "m"}, "amount": 11}
    assert snapshot_target_hash(base) != snapshot_target_hash(changed)


def test_snapshot_rejects_non_json_values():
    with pytest.raises(TypeError, match="not JSON-compatible"):
        canonical_snapshot({"amount": object()})


def test_snapshot_replay_verifies_expected_target_hash():
    snapshot = {"scope": {"chain_id": 56, "market_id": "m"}, "amount": 10}
    target_hash = snapshot_target_hash(snapshot)
    assert verify_snapshot_replay(snapshot, target_hash)
    with pytest.raises(ValueError, match="target_hash mismatch"):
        verify_snapshot_replay(snapshot, "sha256:wrong")


def test_snapshot_replay_result_is_stable_and_auditable():
    snapshot = {"scope": {"chain_id": 56, "market_id": "m"}, "amount": 10}
    result = build_snapshot_replay_result(
        snapshot, gate_allowed=False, reasons=["stale_data", "", "stale_data"]
    )
    assert result.as_dict() == {
        "target_hash": snapshot_target_hash(snapshot),
        "gate_allowed": False,
        "reasons": ["stale_data", "stale_data"],
    }


def test_workflow_replay_fixture_requires_matching_scope_and_generation():
    stages = {
        "scope": {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"},
        "generation_id": "g1",
    }
    fixture = build_workflow_replay_fixture(
        observation=stages, overview=dict(stages), quote=dict(stages), gate_allowed=False,
        reasons=["stale_data"],
    )
    assert fixture["generation_id"] == "g1"
    assert fixture["replay"]["gate_allowed"] is False
    with pytest.raises(ValueError, match="generation mismatch"):
        build_workflow_replay_fixture(
            observation=stages, overview={**stages, "generation_id": "g2"}, quote=stages, gate_allowed=False,
        )


def test_workflow_replay_fixture_round_trips_with_schema_and_hash_validation(tmp_path):
    stages = {
        "scope": {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"},
        "generation_id": "g1",
    }
    fixture = build_workflow_replay_fixture(
        observation=stages, overview=dict(stages), quote=dict(stages), gate_allowed=True,
    )
    path = write_workflow_replay_fixture(tmp_path / "replay.json", fixture)
    assert read_workflow_replay_fixture(path) == fixture
    broken = dict(fixture)
    broken["replay"] = {**fixture["replay"], "target_hash": "sha256:wrong"}
    with pytest.raises(ValueError, match="target_hash mismatch"):
        write_workflow_replay_fixture(tmp_path / "broken.json", broken)
