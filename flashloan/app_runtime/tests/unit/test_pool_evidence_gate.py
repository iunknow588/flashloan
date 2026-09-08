from core.pool_evidence_gate import validate_pool_evidence_for_gate


def _evidence():
    return {
        field: {"status": "ready", "allowed": True, "blocked_reasons": []}
        for field in ("liquidity", "marketability", "profitability", "executability")
    }


def _intent():
    return {
        "scope": {"market_id": "avax-usdc-wavax", "chain_id": 43114, "network": "avalanche", "protocol": "uniswap_v3"},
        "target_hash": "sha256:target",
        "idempotency_key": "run-1",
    }


def test_pool_evidence_gate_accepts_only_complete_local_facts():
    allowed, reasons = validate_pool_evidence_for_gate(_evidence(), _intent(), nonce_reserved=True)
    assert allowed is True
    assert reasons == ()


def test_pool_evidence_gate_preserves_profitability_blocker():
    evidence = _evidence()
    evidence["profitability"] = {"status": "requires_live_quoter", "allowed": False, "blocked_reasons": ["live_quote_required"]}
    allowed, reasons = validate_pool_evidence_for_gate(evidence, _intent(), nonce_reserved=True)
    assert allowed is False
    assert reasons == ("profitability:live_quote_required",)


def test_pool_evidence_gate_reports_contract_errors_without_gate_side_effects():
    evidence = _evidence()
    evidence["liquidity"] = {"allowed": True}
    allowed, reasons = validate_pool_evidence_for_gate(evidence, _intent(), nonce_reserved=True)
    assert allowed is False
    assert "pool_evidence:liquidity_status_missing" in reasons


def test_pool_evidence_gate_blocks_stale_timestamp_when_freshness_required():
    evidence = _evidence()
    evidence["generated_at"] = "2026-01-01T00:00:00Z"
    allowed, reasons = validate_pool_evidence_for_gate(
        evidence,
        _intent(),
        nonce_reserved=True,
        max_pool_evidence_age_seconds=60,
        now=__import__("datetime").datetime(2026, 9, 5, tzinfo=__import__("datetime").timezone.utc),
    )
    assert allowed is False
    assert "pool_evidence:pool_evidence_stale" in reasons
