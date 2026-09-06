import pytest

from core.snapshot_replay import build_workflow_replay_fixture, verify_snapshot_replay
from core.workflow_gate_pipeline import build_observation_to_gate_manifest


SCOPE = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "pancakeswap_v3"}
EVIDENCE = {key: {"status": "allowed", "allowed": True, "blocked_reasons": []} for key in ("liquidity", "marketability", "profitability", "executability")}


def test_observation_to_gate_pipeline_produces_manifest():
    manifest = build_observation_to_gate_manifest(
        scope=SCOPE, generation="g1", observation={"scope": SCOPE, "generation": "g1"},
        overview={"scope": SCOPE, "generation": "g1"}, quote={"scope": SCOPE, "generation": "g1"},
        pool_evidence=EVIDENCE, intent={"idempotency_key": "i1", "deadline": "2999-01-01T00:00:00Z"}, nonce_reserved=True,
    )
    assert manifest["schema"] == "flashloan-evidence-package-v1"
    assert manifest["gate_decision"]["allowed"] is True


def test_observation_to_gate_pipeline_rejects_stage_drift():
    with pytest.raises(ValueError, match="scope mismatch"):
        build_observation_to_gate_manifest(
            scope=SCOPE, generation="g1", observation={"scope": {**SCOPE, "chain_id": 1}}, overview={}, quote={},
            pool_evidence=EVIDENCE, intent={"idempotency_key": "i1", "deadline": "2999-01-01T00:00:00Z"}, nonce_reserved=True,
        )


def test_observation_to_gate_output_round_trips_into_snapshot_replay():
    stages = {
        "scope": SCOPE,
        "generation_id": "g1",
    }
    manifest = build_observation_to_gate_manifest(
        scope=SCOPE,
        generation="g1",
        observation=dict(stages),
        overview=dict(stages),
        quote=dict(stages),
        pool_evidence=EVIDENCE,
        intent={"idempotency_key": "i1", "deadline": "2999-01-01T00:00:00Z"},
        nonce_reserved=True,
    )
    fixture = build_workflow_replay_fixture(
        observation=dict(stages),
        overview=dict(stages),
        quote=dict(stages),
        gate_allowed=manifest["gate_decision"]["allowed"],
    )
    snapshot = {
        "generation_id": fixture["generation_id"],
        "scope": fixture["scope"],
        "stages": fixture["stages"],
    }

    assert fixture["replay"]["gate_allowed"] is True
    assert verify_snapshot_replay(snapshot, fixture["replay"]["target_hash"])
