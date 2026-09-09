from core.error_registry import ERROR_REGISTRY, LEGACY_ERROR_KEYS, ErrorCode, resolve_error_definition
from core.error_stack import ErrorStack, ModuleError


def test_error_registry_codes_and_keys_are_unique():
    definitions = list(ERROR_REGISTRY.values())
    assert len({item.key for item in definitions}) == len(definitions)
    assert len({item.code for item in definitions}) == len(definitions)
    assert int(ErrorCode.OK) == 0
    assert all(item.code == 0 or item.code >= 1000 for item in definitions)


def test_every_definition_has_node_and_retry_policy():
    for item in ERROR_REGISTRY.values():
        assert item.domain
        assert item.flow
        assert item.node_id
        assert item.max_attempts >= 1
        assert item.backoff_seconds >= 0


def test_legacy_state_maps_to_one_global_error_key():
    assert resolve_error_definition(LEGACY_ERROR_KEYS["pool_not_initialized"]).key == (
        "DEX.ONCHAIN.HOP_QUOTE.POOL_NOT_INITIALIZED"
    )
    assert resolve_error_definition("live_quote_failed").code == int(ErrorCode.DEX_LIVE_QUOTE_FAILED)


def test_api_error_stack_can_be_serialized():
    stack = ErrorStack(trace_id="trace-1")
    stack.push(
        ModuleError(
            module="DEX",
            code=int(ErrorCode.DEX_POOL_NOT_INITIALIZED),
            message="hop 1 ETH->TOKEN: pool is not initialized",
        )
    )
    payload = stack.as_dict()
    assert payload["errorStack"][0]["code"] == int(ErrorCode.DEX_POOL_NOT_INITIALIZED)
    assert payload["errorStack"][0]["module"] == "DEX"
    assert "hop 1" in payload["errorStack"][0]["message"]
