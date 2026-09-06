from core.evidence_manifest import (
    MANIFEST_SCHEMA,
    build_evidence_manifest,
    read_evidence_manifest,
    validate_evidence_manifest,
    write_evidence_manifest,
)


def _evidence(allowed=True):
    return {
        key: {"status": "allowed" if allowed else "blocked", "allowed": allowed, "blocked_reasons": [] if allowed else ["stale"]}
        for key in ("liquidity", "marketability", "profitability", "executability")
    }


def test_manifest_has_stable_hash_and_fail_closed_gate():
    kwargs = dict(
        scope={"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "pancakeswap_v3"},
        generation="g1", observation={"rows": []}, overview={"routes": []}, quote={"routes": []},
        pool_evidence=_evidence(), intent={"idempotency_key": "k", "deadline": "2999-01-01T00:00:00Z"}, nonce_reserved=True,
        generated_at=__import__("datetime").datetime(2026, 9, 5, tzinfo=__import__("datetime").timezone.utc),
    )
    first = build_evidence_manifest(**kwargs)
    second = build_evidence_manifest(**kwargs)
    assert first["schema"] == MANIFEST_SCHEMA
    assert first["target_hash"] == second["target_hash"]
    assert first["gate_decision"]["allowed"] is True


def test_manifest_blocks_stale_pool_evidence():
    manifest = build_evidence_manifest(
        scope={"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "pancakeswap_v3"},
        generation="g1", observation={}, overview={}, quote={}, pool_evidence=_evidence(False),
        intent={"idempotency_key": "k", "deadline": "2999-01-01T00:00:00Z"}, nonce_reserved=True,
    )
    assert manifest["gate_decision"]["allowed"] is False


def test_manifest_blocks_absent_or_old_evidence_timestamp_when_requested():
    kwargs = dict(
        scope={"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "pancakeswap_v3"},
        generation="g1", observation={}, overview={}, quote={}, pool_evidence=_evidence(),
        intent={"idempotency_key": "k", "deadline": "2999-01-01T00:00:00Z"}, nonce_reserved=True,
    )
    manifest = build_evidence_manifest(**kwargs, max_pool_evidence_age_seconds=60)
    assert manifest["gate_decision"]["allowed"] is False

    stale_kwargs = dict(kwargs)
    stale_kwargs["pool_evidence"] = {**_evidence(), "generated_at": "2026-01-01T00:00:00Z"}
    stale = build_evidence_manifest(
        **stale_kwargs,
        generated_at=__import__("datetime").datetime(2026, 9, 5, tzinfo=__import__("datetime").timezone.utc),
        max_pool_evidence_age_seconds=60,
    )
    assert "pool_evidence:pool_evidence_stale" in stale["gate_decision"]["reasons"]


def test_evidence_manifest_round_trips_through_json(tmp_path):
    manifest = build_evidence_manifest(
        scope={"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "pancakeswap_v3"},
        generation="g1", observation={"rows": []}, overview={"routes": []}, quote={"routes": []},
        pool_evidence=_evidence(), intent={"idempotency_key": "k", "deadline": "2999-01-01T00:00:00Z"}, nonce_reserved=True,
        generated_at=__import__("datetime").datetime(2026, 9, 5, tzinfo=__import__("datetime").timezone.utc),
    )
    path = tmp_path / "manifest.json"

    write_evidence_manifest(path, manifest, expected_scope={"market_id": "m", "chain_id": 56})
    loaded = read_evidence_manifest(path, expected_scope={"market_id": "m", "chain_id": 56})

    assert loaded["schema"] == MANIFEST_SCHEMA
    assert loaded["target_hash"] == manifest["target_hash"]
    assert loaded["gate_decision"] == manifest["gate_decision"]


def test_evidence_manifest_rejects_scope_drift_and_bad_files(tmp_path):
    manifest = build_evidence_manifest(
        scope={"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "pancakeswap_v3"},
        generation="g1", observation={}, overview={}, quote={}, pool_evidence=_evidence(),
        intent={"idempotency_key": "k", "deadline": "2999-01-01T00:00:00Z"}, nonce_reserved=True,
    )
    path = tmp_path / "manifest.json"
    write_evidence_manifest(path, manifest)

    try:
        read_evidence_manifest(path, expected_scope={"market_id": "other", "chain_id": 56})
    except ValueError as exc:
        assert "scope mismatch" in str(exc)
    else:
        raise AssertionError("expected scope mismatch")

    bad_path = tmp_path / "bad.json"
    bad_path.write_text('{"schema": "wrong"}', encoding="utf-8")
    try:
        read_evidence_manifest(bad_path)
    except ValueError as exc:
        assert "schema" in str(exc)
    else:
        raise AssertionError("expected schema rejection")


def test_validate_evidence_manifest_rejects_non_manifest_object():
    try:
        validate_evidence_manifest({"schema": MANIFEST_SCHEMA})
    except ValueError as exc:
        assert "target_hash" in str(exc)
    else:
        raise AssertionError("expected target_hash rejection")
