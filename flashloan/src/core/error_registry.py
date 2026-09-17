"""Project-local BOT/DEX/CORE error code tables.

These codes are owned by the flashloan project, not by the reusable
``errorstack`` package.  The generic stack never reads this registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class ErrorCode(IntEnum):
    OK = 0

    CORE_VALIDATION = 1001
    CORE_SCOPE_MISMATCH = 1002
    CORE_FLOW_MISSING = 1003
    CORE_INTERNAL = 1099

    BOT_ACCOUNT_NOT_LIQUIDATABLE = 2001
    BOT_NO_EXECUTABLE_CANDIDATE = 2002
    BOT_QUOTE_FAILED = 2003
    BOT_STATIC_CALL_FAILED = 2004
    BOT_APPROVAL_FAILED = 2005
    BOT_SUBMISSION_REVERTED = 2006
    BOT_RECEIPT_FAILED = 2007
    BOT_EXECUTION_BLOCKED = 2008
    BOT_INTERNAL = 2099

    DEX_LIVE_QUOTE_FAILED = 3001
    DEX_POOL_NOT_INITIALIZED = 3002
    DEX_NOT_ENOUGH_LIQUIDITY = 3003
    DEX_SIZE_POOL_NOT_INITIALIZED = 3004
    DEX_SIZE_NOT_PROFITABLE = 3005
    DEX_GATE_NOT_READY = 3006
    DEX_ROUTE_INVALID = 3007
    DEX_RPC_FAILED = 3008
    DEX_INTERNAL = 3099


@dataclass(frozen=True)
class ErrorDefinition:
    key: str
    code: int
    domain: str
    flow: str
    node_id: str
    retryable: bool
    max_attempts: int
    backoff_seconds: float
    manual_review: bool


def _definition(
    key: str,
    code: int | ErrorCode,
    domain: str,
    flow: str,
    node_id: str,
    *,
    retryable: bool = False,
    max_attempts: int = 1,
    backoff_seconds: float = 0.0,
    manual_review: bool = False,
) -> ErrorDefinition:
    return ErrorDefinition(
        key=key,
        code=int(code),
        domain=domain,
        flow=flow,
        node_id=node_id,
        retryable=retryable,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
        manual_review=manual_review,
    )


CONTRACT_DEX_MODULE_OFFSET = 40_000_000

CONTRACT_DEX_FLOW_CODES: dict[str, int] = {
    "validation": 1,
    "quote": 2,
    "execution": 3,
    "repayment": 4,
    "callback": 5,
    "sweep": 6,
    "internal": 9,
}

CONTRACT_DEX_NODE_CODES: dict[str, int] = {
    "request_validate": 1,
    "flash_source_resolve": 2,
    "adapter_resolve": 3,
    "route_validate": 4,
    "hop_quote": 5,
    "swap_execute": 6,
    "callback_verify": 7,
    "repayment_check": 8,
    "transfer": 9,
    "approve": 10,
    "reentrancy_guard": 11,
    "owner_guard": 12,
    "pool_guard": 13,
    "initiator_guard": 14,
    "paused_guard": 15,
    "internal": 99,
}


def _contract_definition(
    key: str,
    flow: str,
    node_id: str,
    detail_code: int = 1,
    *,
    retryable: bool = False,
    max_attempts: int = 1,
    backoff_seconds: float = 0.0,
    manual_review: bool = False,
) -> ErrorDefinition:
    flow_code = CONTRACT_DEX_FLOW_CODES[flow]
    node_code = CONTRACT_DEX_NODE_CODES[node_id]
    code = CONTRACT_DEX_MODULE_OFFSET + flow_code * 10_000 + node_code * 100 + detail_code
    return _definition(
        key,
        code,
        "CONTRACT.DEX",
        flow,
        node_id,
        retryable=retryable,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
        manual_review=manual_review,
    )


ERROR_REGISTRY: dict[str, ErrorDefinition] = {
    "CORE.OK": _definition("CORE.OK", ErrorCode.OK, "CORE", "system", "complete"),
    "CORE.VALIDATION.INVALID": _definition("CORE.VALIDATION.INVALID", ErrorCode.CORE_VALIDATION, "CORE", "validation", "validate_request"),
    "CORE.SCOPE.MISMATCH": _definition("CORE.SCOPE.MISMATCH", ErrorCode.CORE_SCOPE_MISMATCH, "CORE", "scope", "validate_scope"),
    "CORE.FLOW.MISSING": _definition("CORE.FLOW.MISSING", ErrorCode.CORE_FLOW_MISSING, "CORE", "dispatch", "resolve_flow"),
    "CORE.INTERNAL": _definition("CORE.INTERNAL", ErrorCode.CORE_INTERNAL, "CORE", "system", "internal", manual_review=True),
    "BOT.LIQUIDATION.ACCOUNT_NOT_LIQUIDATABLE": _definition("BOT.LIQUIDATION.ACCOUNT_NOT_LIQUIDATABLE", ErrorCode.BOT_ACCOUNT_NOT_LIQUIDATABLE, "BOT", "liquidation", "account_check"),
    "BOT.LIQUIDATION.NO_EXECUTABLE_CANDIDATE": _definition("BOT.LIQUIDATION.NO_EXECUTABLE_CANDIDATE", ErrorCode.BOT_NO_EXECUTABLE_CANDIDATE, "BOT", "liquidation", "candidate_selection"),
    "BOT.LIQUIDATION.QUOTE_FAILED": _definition("BOT.LIQUIDATION.QUOTE_FAILED", ErrorCode.BOT_QUOTE_FAILED, "BOT", "liquidation", "quote"),
    "BOT.LIQUIDATION.STATIC_CALL_FAILED": _definition("BOT.LIQUIDATION.STATIC_CALL_FAILED", ErrorCode.BOT_STATIC_CALL_FAILED, "BOT", "liquidation", "static_call", manual_review=True),
    "BOT.EXECUTION.APPROVAL_FAILED": _definition("BOT.EXECUTION.APPROVAL_FAILED", ErrorCode.BOT_APPROVAL_FAILED, "BOT", "execution", "approval", retryable=True, max_attempts=2, backoff_seconds=0.5),
    "BOT.EXECUTION.SUBMISSION_REVERTED": _definition("BOT.EXECUTION.SUBMISSION_REVERTED", ErrorCode.BOT_SUBMISSION_REVERTED, "BOT", "execution", "submission", manual_review=True),
    "BOT.EXECUTION.RECEIPT_FAILED": _definition("BOT.EXECUTION.RECEIPT_FAILED", ErrorCode.BOT_RECEIPT_FAILED, "BOT", "execution", "receipt", retryable=True, max_attempts=2, backoff_seconds=1.0),
    "BOT.EXECUTION.BLOCKED": _definition("BOT.EXECUTION.BLOCKED", ErrorCode.BOT_EXECUTION_BLOCKED, "BOT", "execution", "submission_gate", manual_review=True),
    "BOT.INTERNAL": _definition("BOT.INTERNAL", ErrorCode.BOT_INTERNAL, "BOT", "system", "internal", manual_review=True),
    "DEX.QUOTE.LIVE_QUOTE_FAILED": _definition("DEX.QUOTE.LIVE_QUOTE_FAILED", ErrorCode.DEX_LIVE_QUOTE_FAILED, "DEX", "quote", "live_quote", retryable=True, max_attempts=2, backoff_seconds=1.0),
    "DEX.ONCHAIN.HOP_QUOTE.POOL_NOT_INITIALIZED": _definition("DEX.ONCHAIN.HOP_QUOTE.POOL_NOT_INITIALIZED", ErrorCode.DEX_POOL_NOT_INITIALIZED, "DEX", "onchain_quote", "hop_quote", manual_review=True),
    "DEX.ONCHAIN.HOP_QUOTE.NOT_ENOUGH_LIQUIDITY": _definition("DEX.ONCHAIN.HOP_QUOTE.NOT_ENOUGH_LIQUIDITY", ErrorCode.DEX_NOT_ENOUGH_LIQUIDITY, "DEX", "onchain_quote", "hop_quote", manual_review=True),
    "DEX.SIZE_PROBE.POOL_NOT_INITIALIZED": _definition("DEX.SIZE_PROBE.POOL_NOT_INITIALIZED", ErrorCode.DEX_SIZE_POOL_NOT_INITIALIZED, "DEX", "size_probe", "probe_quote", manual_review=True),
    "DEX.SIZE_PROBE.NOT_PROFITABLE_AFTER_COSTS": _definition("DEX.SIZE_PROBE.NOT_PROFITABLE_AFTER_COSTS", ErrorCode.DEX_SIZE_NOT_PROFITABLE, "DEX", "size_probe", "profit_gate"),
    "DEX.GATE.NOT_READY": _definition("DEX.GATE.NOT_READY", ErrorCode.DEX_GATE_NOT_READY, "DEX", "preflight", "execution_gate", manual_review=True),
    "DEX.ROUTE.INVALID": _definition("DEX.ROUTE.INVALID", ErrorCode.DEX_ROUTE_INVALID, "DEX", "route", "route_validation"),
    "DEX.RPC.FAILED": _definition("DEX.RPC.FAILED", ErrorCode.DEX_RPC_FAILED, "DEX", "rpc", "rpc_call", retryable=True, max_attempts=3, backoff_seconds=0.5),
    "DEX.INTERNAL": _definition("DEX.INTERNAL", ErrorCode.DEX_INTERNAL, "DEX", "system", "internal", manual_review=True),
    "CONTRACT.DEX.VALIDATION.REQUEST_INVALID": _contract_definition("CONTRACT.DEX.VALIDATION.REQUEST_INVALID", "validation", "request_validate"),
    "CONTRACT.DEX.VALIDATION.FLASH_SOURCE_MISSING": _contract_definition("CONTRACT.DEX.VALIDATION.FLASH_SOURCE_MISSING", "validation", "flash_source_resolve"),
    "CONTRACT.DEX.VALIDATION.ROUTE_INVALID": _contract_definition("CONTRACT.DEX.VALIDATION.ROUTE_INVALID", "validation", "route_validate"),
    "CONTRACT.DEX.QUOTE.ADAPTER_MISSING": _contract_definition("CONTRACT.DEX.QUOTE.ADAPTER_MISSING", "quote", "adapter_resolve"),
    "CONTRACT.DEX.QUOTE.HOP_QUOTE.POOL_NOT_INITIALIZED": _contract_definition("CONTRACT.DEX.QUOTE.HOP_QUOTE.POOL_NOT_INITIALIZED", "quote", "hop_quote", 1, manual_review=True),
    "CONTRACT.DEX.QUOTE.HOP_QUOTE.NOT_ENOUGH_LIQUIDITY": _contract_definition("CONTRACT.DEX.QUOTE.HOP_QUOTE.NOT_ENOUGH_LIQUIDITY", "quote", "hop_quote", 2, manual_review=True),
    "CONTRACT.DEX.QUOTE.HOP_QUOTE.QUOTER_FAILED": _contract_definition("CONTRACT.DEX.QUOTE.HOP_QUOTE.QUOTER_FAILED", "quote", "hop_quote", 3, retryable=True, max_attempts=2, backoff_seconds=1.0),
    "CONTRACT.DEX.EXECUTION.ROUTER_CALL_FAILED": _contract_definition("CONTRACT.DEX.EXECUTION.ROUTER_CALL_FAILED", "execution", "swap_execute", manual_review=True),
    "CONTRACT.DEX.EXECUTION.APPROVE_FAILED": _contract_definition("CONTRACT.DEX.EXECUTION.APPROVE_FAILED", "execution", "approve", retryable=True, max_attempts=2, backoff_seconds=0.5),
    "CONTRACT.DEX.EXECUTION.TRANSFER_FAILED": _contract_definition("CONTRACT.DEX.EXECUTION.TRANSFER_FAILED", "execution", "transfer", manual_review=True),
    "CONTRACT.DEX.EXECUTION.REENTRANCY": _contract_definition("CONTRACT.DEX.EXECUTION.REENTRANCY", "execution", "reentrancy_guard"),
    "CONTRACT.DEX.EXECUTION.NOT_OWNER": _contract_definition("CONTRACT.DEX.EXECUTION.NOT_OWNER", "execution", "owner_guard"),
    "CONTRACT.DEX.EXECUTION.PAUSED": _contract_definition("CONTRACT.DEX.EXECUTION.PAUSED", "execution", "paused_guard"),
    "CONTRACT.DEX.CALLBACK.BAD_FLASH_CALLBACK": _contract_definition("CONTRACT.DEX.CALLBACK.BAD_FLASH_CALLBACK", "callback", "callback_verify"),
    "CONTRACT.DEX.CALLBACK.NOT_POOL": _contract_definition("CONTRACT.DEX.CALLBACK.NOT_POOL", "callback", "pool_guard"),
    "CONTRACT.DEX.CALLBACK.BAD_INITIATOR": _contract_definition("CONTRACT.DEX.CALLBACK.BAD_INITIATOR", "callback", "initiator_guard"),
    "CONTRACT.DEX.REPAYMENT.INSUFFICIENT": _contract_definition("CONTRACT.DEX.REPAYMENT.INSUFFICIENT", "repayment", "repayment_check", manual_review=True),
    "CONTRACT.DEX.INTERNAL": _contract_definition("CONTRACT.DEX.INTERNAL", "internal", "internal", manual_review=True),
}

LEGACY_ERROR_KEYS: dict[str, str] = {
    "validation": "CORE.VALIDATION.INVALID",
    "scope_mismatch": "CORE.SCOPE.MISMATCH",
    "chain_scope_required": "CORE.SCOPE.MISMATCH",
    "invalid_chain_flow_handler": "CORE.FLOW.MISSING",
    "account_not_liquidatable": "BOT.LIQUIDATION.ACCOUNT_NOT_LIQUIDATABLE",
    "no_liquidation_candidate": "BOT.LIQUIDATION.NO_EXECUTABLE_CANDIDATE",
    "quote_failed": "BOT.LIQUIDATION.QUOTE_FAILED",
    "static_call_failed": "BOT.LIQUIDATION.STATIC_CALL_FAILED",
    "approval_failed": "BOT.EXECUTION.APPROVAL_FAILED",
    "submission_reverted": "BOT.EXECUTION.SUBMISSION_REVERTED",
    "live_quote_failed": "DEX.QUOTE.LIVE_QUOTE_FAILED",
    "pool_not_initialized": "DEX.ONCHAIN.HOP_QUOTE.POOL_NOT_INITIALIZED",
    "not_enough_liquidity": "DEX.ONCHAIN.HOP_QUOTE.NOT_ENOUGH_LIQUIDITY",
    "not_profitable_after_costs": "DEX.SIZE_PROBE.NOT_PROFITABLE_AFTER_COSTS",
    "route_unprofitable": "DEX.SIZE_PROBE.NOT_PROFITABLE_AFTER_COSTS",
    "execution_reverted": "BOT.EXECUTION.SUBMISSION_REVERTED",
    "rpc": "DEX.RPC.FAILED",
    "risk_blocked": "DEX.GATE.NOT_READY",
}


def resolve_error_definition(error_key: str | None) -> ErrorDefinition:
    key = str(error_key or "").strip()
    if key in ERROR_REGISTRY:
        return ERROR_REGISTRY[key]
    mapped = LEGACY_ERROR_KEYS.get(key.lower(), "CORE.INTERNAL")
    return ERROR_REGISTRY[mapped]


def contract_dex_error_codes() -> dict[str, int]:
    return {
        key: definition.code
        for key, definition in ERROR_REGISTRY.items()
        if definition.domain == "CONTRACT.DEX"
    }


__all__ = [
    "CONTRACT_DEX_MODULE_OFFSET",
    "CONTRACT_DEX_FLOW_CODES",
    "CONTRACT_DEX_NODE_CODES",
    "ERROR_REGISTRY",
    "ErrorCode",
    "ErrorDefinition",
    "LEGACY_ERROR_KEYS",
    "contract_dex_error_codes",
    "resolve_error_definition",
]
