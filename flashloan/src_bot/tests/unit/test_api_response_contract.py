from core.api_response_contract import (
    error_response,
    missing_scope_response,
    success_response,
    wrong_scope_response,
)


def test_success_response_has_stable_envelope_and_copies_scope():
    scope = {"network": "bnb", "chain_id": 56}
    payload = success_response({"items": [1]}, scope=scope)

    assert payload == {"ok": True, "scope": scope, "items": [1]}
    assert payload["scope"] is not scope


def test_error_response_has_structured_error_and_status():
    payload = error_response("bad_request", "invalid input", status=422)

    assert payload == {
        "ok": False,
        "error": {"code": "bad_request", "message": "invalid input"},
        "status": 422,
    }


def test_scope_error_variants_are_distinct_and_fail_closed():
    missing = missing_scope_response()
    wrong = wrong_scope_response(scope={"network": "bnb", "chain_id": 56})

    assert missing["error"]["code"] == "missing_scope"
    assert missing["status"] == 400
    assert wrong["error"]["code"] == "wrong_scope"
    assert wrong["status"] == 409
    assert wrong["scope"]["chain_id"] == 56


def test_route_error_uses_structured_error_code():
    from core.api_response_contract import error_response

    payload = error_response("chain_selection_invalid", "invalid chain", status=400)

    assert payload["ok"] is False
    assert payload["error"] == {"code": "chain_selection_invalid", "message": "invalid chain"}
    assert payload["status"] == 400
