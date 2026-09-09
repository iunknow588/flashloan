from core.api_response_contract import error_response, success_response
from core.error_registry import ErrorCode
from core.error_stack import ErrorStack, ModuleError
from core.errors import error_payload
from bot.execution.revert_parser import parse_revert_reason
from bot.liquidation.route_response import classify_liquidation_payload_block


def test_stack_collects_module_errors_in_push_order():
    stack = ErrorStack(trace_id="t1")
    stack.push(ModuleError(module="BOT", code=0, message="scope ok, not an error"))
    stack.push(
        ModuleError(
            module="BOT",
            code=int(ErrorCode.BOT_STATIC_CALL_FAILED),
            message="static call reverted",
        )
    )
    assert [item["message"] for item in stack.as_list()] == ["static call reverted"]
    assert "not an error" not in stack.as_dict()["errorStack"][0]["message"]


def test_module_error_body_keeps_call_site_in_message_for_repeated_calls():
    stack = ErrorStack(trace_id="route-1")
    stack.push(
        ModuleError(
            module="DEX",
            code=int(ErrorCode.DEX_POOL_NOT_INITIALIZED),
            message="hop 1 ETH->TOKEN: V4 pool is not initialized",
        )
    )
    first = stack.as_list()[0]
    assert first["code"] == int(ErrorCode.DEX_POOL_NOT_INITIALIZED)
    assert "hop 1" in first["message"]
    assert "ETH->TOKEN" in first["message"]


def test_error_payload_preserves_legacy_fields_and_adds_global_stack():
    payload = error_payload("rpc", "timeout", trace_id="t1")
    assert payload["error_code"] == "rpc"
    assert payload["code"] == int(ErrorCode.DEX_RPC_FAILED)
    assert payload["errorKey"] == "DEX.RPC.FAILED"
    assert payload["traceId"] == "t1"
    assert payload["errorStack"][0]["module"] == "DEX"
    assert payload["errorStack"][0]["message"] == "timeout"
    assert payload["retry"]["retryable"] is True


def test_api_response_contract_accepts_error_stack():
    stack = ErrorStack(trace_id="api-1")
    stack.push(
        ModuleError(
            module="CORE",
            code=int(ErrorCode.CORE_SCOPE_MISMATCH),
            message="wrong scope",
        )
    )
    failure = error_response("scope_mismatch", "wrong scope", stack=stack)
    success = success_response({"value": 1}, stack=stack)
    assert failure["ok"] is False
    assert failure["errorStack"][0]["code"] == int(ErrorCode.CORE_SCOPE_MISMATCH)
    assert success["ok"] is True
    assert success["errorStack"][0]["message"] == "wrong scope"


def test_bot_blocking_response_exposes_minimal_error_stack():
    response = classify_liquidation_payload_block(
        "0xabc",
        {"summary": {"health_factor": 1.25}},
    )
    assert response["errorStack"][0]["code"] == int(ErrorCode.BOT_ACCOUNT_NOT_LIQUIDATABLE)
    assert response["errorStack"][0]["module"] == "BOT"
    assert set(response["errorStack"][0].keys()) == {"module", "code", "message"}


def test_bot_revert_parser_exposes_global_error_key():
    parsed = parse_revert_reason("execution reverted: ProfitTooLow()")
    assert parsed["category"] == "profit_too_low"
    assert parsed["error_key"] == "BOT.EXECUTION.SUBMISSION_REVERTED"
    assert parsed["module"] == "BOT.revert_parser"
    assert parsed["message"] == "利润不足（swap后收益低于minProfitAmount）"
    assert isinstance(parsed["code"], int)
