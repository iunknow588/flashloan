from datetime import datetime, timezone

from core.gate_decision import validate_execution_gate


def _intent(**overrides):
    value = {
        "scope": {"market_id": "avax-usdc-wavax", "chain_id": 43114, "network": "avalanche", "protocol": "uniswap_v3"},
        "target_hash": "sha256:target",
        "idempotency_key": "run-1",
        "deadline": "2030-01-01T00:00:00Z",
    }
    value.update(overrides)
    return value


def test_gate_accepts_valid_intent_with_reserved_nonce():
    allowed, reasons = validate_execution_gate(_intent(), now=datetime(2029, 1, 1, tzinfo=timezone.utc), nonce_reserved=True)
    assert allowed is True
    assert reasons == ()


def test_gate_fail_closed_for_scope_deadline_and_nonce():
    allowed, reasons = validate_execution_gate(
        _intent(scope={"market_id": "", "chain_id": 43114}, deadline="2020-01-01T00:00:00Z"),
        now=datetime(2029, 1, 1, tzinfo=timezone.utc),
    )
    assert allowed is False
    assert set(reasons) == {"invalid_scope", "deadline_expired", "nonce_not_reserved"}


def test_gate_rejects_reused_idempotency_key():
    allowed, reasons = validate_execution_gate(_intent(), nonce_reserved=True, seen_idempotency_keys={"run-1"})
    assert allowed is False
    assert reasons == ("idempotency_key_reused",)
