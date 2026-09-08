from core.pool_evidence_contract import validate_pool_evidence_contract


def _payload():
    return {
        field: {"status": "ready", "allowed": True, "blocked_reasons": []}
        for field in ("liquidity", "marketability", "profitability", "executability")
    }


def test_pool_evidence_contract_accepts_complete_payload():
    assert validate_pool_evidence_contract(_payload()) == (True, ())


def test_pool_evidence_contract_rejects_missing_field_and_bad_types():
    payload = _payload()
    del payload["liquidity"]
    payload["marketability"] = {"status": "blocked", "allowed": False, "blocked_reasons": []}
    allowed, reasons = validate_pool_evidence_contract(payload)
    assert allowed is False
    assert "liquidity_missing_or_not_object" in reasons
    assert "marketability_blocked_without_reason" in reasons


def test_pool_evidence_contract_rejects_allowed_field_with_blockers():
    payload = _payload()
    payload["executability"] = {"status": "ready", "allowed": True, "blocked_reasons": ["execution_disabled"]}
    allowed, reasons = validate_pool_evidence_contract(payload)
    assert allowed is False
    assert reasons == ("executability_allowed_with_blocked_reasons",)
