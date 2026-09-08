from core.dry_run_gate import consume_evidence_manifest
from core.evidence_manifest import build_evidence_manifest


def _evidence():
    return {
        key: {"status": "allowed", "allowed": True, "blocked_reasons": []}
        for key in ("liquidity", "marketability", "profitability", "executability")
    }


def _manifest():
    return build_evidence_manifest(
        scope={"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "pancakeswap_v3"},
        generation="g1",
        observation={},
        overview={},
        quote={},
        pool_evidence=_evidence(),
        intent={"idempotency_key": "i1", "deadline": "2999-01-01T00:00:00Z"},
        nonce_reserved=True,
    )


def test_dry_run_consumes_allowed_manifest():
    decision = consume_evidence_manifest(_manifest())
    assert decision.allowed is True
    assert decision.target_hash.startswith("sha256:")
    assert decision.reasons == ()


def test_dry_run_extra_blocker_flips_decision_and_remains_auditable():
    manifest = _manifest()
    decision = consume_evidence_manifest(manifest, extra_blockers=["environment_not_approved"])
    assert decision.allowed is False
    assert "environment_not_approved" in decision.reasons
    assert decision.as_dict()["target_hash"] == manifest["target_hash"]
