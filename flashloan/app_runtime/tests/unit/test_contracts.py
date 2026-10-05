from core.contracts import (ExecutionGate, ExecutionIntent, EvidenceEnvelope, GateDecision, MarketScope, normalize_data_source, source_metadata)


from core.contracts import scope_matches, validate_scope_contract, validate_scoped_payload


def test_market_scope_and_execution_gate_have_stable_wire_shapes():
    scope = MarketScope(
        market_id="robinhood-dex-shadow",
        chain_id=4663,
        network="robinhood",
        feature="dex_arbitrage",
        workflow="dex_arbitrage",
        execution_mode="cow",
        scope_type="single_chain",
    )
    gate = ExecutionGate(
        ready=False,
        execution_enabled=False,
        broadcast_enabled=False,
        blocked_reasons=("robinhood_shadow_observation_only",),
        evidence_refs=("evidence/readiness.json",),
    )

    assert scope.as_dict()["market_id"] == "dex-arbitrage-4663-uniswap-v4"
    assert scope.as_dict()["legacy_market_id"] == "robinhood-dex-shadow"
    assert scope.as_dict()["global_chain_id"] == "chain:4663"
    assert scope.as_dict()["chain_key"] == "chain:4663"
    assert scope.as_dict()["scope_type"] == "single_chain"
    assert gate.as_dict()["blocked_reasons"] == ["robinhood_shadow_observation_only"]
    assert gate.as_dict()["evidence_refs"] == ["evidence/readiness.json"]


def test_data_source_metadata_does_not_allow_unregistered_source_names():
    assert normalize_data_source("database") == "database"
    assert normalize_data_source("made-up-source") == "builtin_fallback"
    assert source_metadata("runtime_cache", freshness="2026-08-19T00:00:00Z") == {
        "source": "runtime_cache",
        "freshness": "2026-08-19T00:00:00Z",
        "fallback_reason": None,
    }


def test_evidence_envelope_rejects_unknown_source_and_status():
    import pytest

    with pytest.raises(ValueError, match="unknown evidence source"):
        EvidenceEnvelope("e1", "unknown", "2026-09-04T00:00:00Z")
    with pytest.raises(ValueError, match="unknown evidence status"):
        EvidenceEnvelope("e1", "runtime_cache", "2026-09-04T00:00:00Z", status="expired")


def test_execution_intent_requires_complete_scope_and_identity():
    import pytest

    with pytest.raises(ValueError, match="execution scope invalid.*scope missing"):
        ExecutionIntent("liquidation", {"market_id": "m"}, "hash", "idem")
    with pytest.raises(ValueError, match="execution scope invalid.*chain_id must be integer"):
        ExecutionIntent(
            "liquidation",
            {"market_id": "m", "chain_id": "latest", "network": "bnb", "protocol": "venus"},
            "hash", "idem",
        )
    with pytest.raises(ValueError, match="execution scope invalid.*scope missing: protocol"):
        ExecutionIntent(
            "liquidation",
            {"market_id": "m", "chain_id": 56, "network": "bnb"},
            "hash", "idem",
        )
    intent = ExecutionIntent(
        "dex_arbitrage",
        {"market_id": "m", "chain_id": 46630, "network": "robinhood-testnet", "protocol": "uniswap_v4"},
        "hash", "idem", ("e1",),
    )
    assert intent.as_dict()["scope"]["chain_id"] == 46630


def test_scope_contract_rejects_missing_fields_and_mismatch():
    import pytest

    with pytest.raises(ValueError, match="scope missing: market_id"):
        validate_scope_contract(
            market_id="",
            chain_id=46630,
            network="robinhood-testnet",
            protocol="uniswap_v4",
        )
    with pytest.raises(ValueError, match="scope missing: chain_id"):
        validate_scope_contract(
            market_id="m",
            chain_id=None,
            network="robinhood-testnet",
            protocol="uniswap_v4",
        )
    valid = validate_scope_contract(
        market_id="m",
        chain_id=46630,
        network="Robinhood-Testnet",
        protocol="Uniswap_V4",
    )
    assert valid["network"] == "robinhood-testnet"
    assert valid["protocol"] == "uniswap_v4"
    assert valid["chain_id"] == 46630

    with pytest.raises(ValueError, match="scope mismatch on chain_id"):
        scope_matches(
            valid,
            {"market_id": "m", "chain_id": 4663, "network": "robinhood-testnet", "protocol": "uniswap_v4"},
        )


def test_gate_decision_is_auditable():
    decision = GateDecision("freshness", False, ("stale_data",), ("e1",), trace_id="t1")
    assert decision.as_dict()["allowed"] is False
    assert decision.as_dict()["trace_id"] == "t1"


def test_scoped_payload_requires_matching_persisted_scope():
    expected = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    assert validate_scoped_payload({"scope": expected, "value": 1}, expected) == expected
    import pytest
    with pytest.raises(ValueError, match="scope missing: market_id, chain_id, network, protocol"):
        validate_scoped_payload({"value": 1}, expected)
    with pytest.raises(ValueError, match="scope mismatch on chain_id"):
        validate_scoped_payload({"scope": {**expected, "chain_id": 1}}, expected)
