from core.error_registry import (
    CONTRACT_DEX_FLOW_CODES,
    CONTRACT_DEX_MODULE_OFFSET,
    CONTRACT_DEX_NODE_CODES,
    ERROR_REGISTRY,
    LEGACY_ERROR_KEYS,
    ErrorCode,
    contract_dex_error_codes,
    resolve_error_definition,
)
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


def test_contract_dex_registry_codes_are_unique_and_scoped_to_flow_node_ids():
    codes = contract_dex_error_codes()
    assert codes
    assert len({item.code for item in ERROR_REGISTRY.values()}) == len(ERROR_REGISTRY)

    for key, code in codes.items():
        assert key.startswith("CONTRACT.DEX.")
        definition = ERROR_REGISTRY[key]
        assert definition.domain == "CONTRACT.DEX"
        assert definition.flow in CONTRACT_DEX_FLOW_CODES
        assert definition.node_id in CONTRACT_DEX_NODE_CODES
        assert code >= CONTRACT_DEX_MODULE_OFFSET
        assert code < CONTRACT_DEX_MODULE_OFFSET + 1_000_000
        assert code == (
            CONTRACT_DEX_MODULE_OFFSET
            + CONTRACT_DEX_FLOW_CODES[definition.flow] * 10_000
            + CONTRACT_DEX_NODE_CODES[definition.node_id] * 100
            + code % 100
        )


def test_contract_dex_registry_contains_expected_contract_failure_points():
    codes = contract_dex_error_codes()
    assert codes["CONTRACT.DEX.VALIDATION.REQUEST_INVALID"] == 40_010_101
    assert codes["CONTRACT.DEX.QUOTE.HOP_QUOTE.POOL_NOT_INITIALIZED"] == 40_020_501
    assert codes["CONTRACT.DEX.EXECUTION.ROUTER_CALL_FAILED"] == 40_030_601
    assert codes["CONTRACT.DEX.CALLBACK.BAD_FLASH_CALLBACK"] == 40_050_701


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
