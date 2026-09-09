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
    code: ErrorCode,
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


__all__ = [
    "ERROR_REGISTRY",
    "ErrorCode",
    "ErrorDefinition",
    "LEGACY_ERROR_KEYS",
    "resolve_error_definition",
]
