from core.contracts import ExecutionGate, MarketScope, normalize_data_source, source_metadata


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

    assert scope.as_dict()["market_id"] == "dex-arbitrage-4663-uniswap-v3"
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
