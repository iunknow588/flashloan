from core.errors import ERROR_CODES, RETRY_POLICIES, error_payload, normalize_error_code


def test_error_codes_are_stable_and_known():
    assert "validation" in ERROR_CODES
    assert "risk_blocked" in ERROR_CODES
    assert len(RETRY_POLICIES) == len(ERROR_CODES)


def test_unknown_error_code_falls_back_to_validation():
    assert normalize_error_code("made-up") == "validation"


def test_error_payload_carries_policy_and_trace():
    payload = error_payload("rpc", "timeout", trace_id="t1", details={"rpc": "primary"})
    assert payload["error_code"] == "rpc"
    assert payload["trace_id"] == "t1"
    assert payload["details"] == {"rpc": "primary"}
    assert payload["retryable"] is True
    assert payload["manual_review"] is False

    blocked = error_payload("risk_blocked", "bad evidence")
    assert blocked["error_code"] == "risk_blocked"
    assert blocked["retryable"] is False
    assert blocked["manual_review"] is True
