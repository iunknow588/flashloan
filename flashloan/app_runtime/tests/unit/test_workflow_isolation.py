from core.workflow_isolation import validate_workflow_isolation


def test_workflow_isolation_requires_matching_queue_lock_and_idempotency():
    valid, reasons = validate_workflow_isolation({"workflow": "cow", "queue": "cow", "lock_key": "lock:cow", "idempotency_key": "i1"}, expected_workflow="cow")
    assert valid and reasons == ()

    valid, reasons = validate_workflow_isolation({"workflow": "liquidation", "queue": "cow", "lock_key": "lock:liquidation"}, expected_workflow="cow")
    assert not valid
    assert {"workflow_mismatch", "queue_workflow_mismatch", "idempotency_key_missing"}.issubset(reasons)
