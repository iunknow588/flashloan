from core.api_response_contract import (
    error_response,
    missing_scope_response,
    success_response,
    wrong_scope_response,
)
from core.error_stack import ErrorStack, ModuleError


def test_success_response_has_stable_envelope_and_copies_scope():
    scope = {"network": "bnb", "chain_id": 56}
    payload = success_response({"items": [1]}, scope=scope)

    assert payload["ok"] is True
    assert payload["scope"] == scope
    assert payload["scope"] is not scope
    assert payload["items"] == [1]
    assert payload["errorStack"] == [{"module": "CORE", "code": 0, "message": "OK"}]
    assert payload["scope"] is not scope


def test_error_response_has_structured_error_and_status():
    payload = error_response("bad_request", "invalid input", status=422)

    assert payload["ok"] is False
    assert payload["error"] == {"code": "bad_request", "message": "invalid input"}
    assert payload["status"] == 422
    assert payload["errorStack"][0]["module"] == "CORE"
    assert payload["errorStack"][0]["code"] > 0
    assert payload["errorStack"][0]["message"] == "invalid input"


def test_scope_error_variants_are_distinct_and_fail_closed():
    missing = missing_scope_response()
    wrong = wrong_scope_response(scope={"network": "bnb", "chain_id": 56})

    assert missing["error"]["code"] == "missing_scope"
    assert missing["status"] == 400
    assert wrong["error"]["code"] == "wrong_scope"
    assert wrong["status"] == 409
    assert wrong["scope"]["chain_id"] == 56
    assert missing["errorStack"][0]["message"] == "scope is required"
    assert wrong["errorStack"][0]["message"] == "scope does not match the requested resource"


def test_route_error_uses_structured_error_code():
    from core.api_response_contract import error_response

    payload = error_response("chain_selection_invalid", "invalid chain", status=400)

    assert payload["ok"] is False
    assert payload["error"] == {"code": "chain_selection_invalid", "message": "invalid chain"}
    assert payload["status"] == 400
    assert payload["errorStack"][0]["code"] > 0


def test_success_response_replaces_internal_ok_with_boundary_ok():
    stack = ErrorStack(trace_id="internal-1")
    stack.push(ModuleError(module="internal.detail", code=0, message="ok"))
    payload = success_response({"value": 1}, stack=stack)
    assert payload["ok"] is True
    assert payload["errorStack"] == [{"module": "CORE", "code": 0, "message": "OK"}]
