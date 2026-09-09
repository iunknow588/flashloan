"""Shared error taxonomy and retry policy helpers."""

from __future__ import annotations

from typing import Any

from core.error_registry import ErrorCode, resolve_error_definition
from core.error_stack import ErrorStack, ModuleError, stack_from_legacy


ERROR_CODES = (
    "validation",
    "unsupported",
    "scope_mismatch",
    "stale_data",
    "rpc",
    "quote",
    "db",
    "revert",
    "risk_blocked",
)


RETRY_POLICIES: dict[str, dict[str, Any]] = {
    "validation": {"retryable": False, "max_attempts": 1, "backoff_seconds": 0.0, "manual_review": False},
    "unsupported": {"retryable": False, "max_attempts": 1, "backoff_seconds": 0.0, "manual_review": False},
    "scope_mismatch": {"retryable": False, "max_attempts": 1, "backoff_seconds": 0.0, "manual_review": False},
    "stale_data": {"retryable": True, "max_attempts": 3, "backoff_seconds": 1.0, "manual_review": False},
    "rpc": {"retryable": True, "max_attempts": 3, "backoff_seconds": 0.5, "manual_review": False},
    "quote": {"retryable": True, "max_attempts": 2, "backoff_seconds": 1.0, "manual_review": False},
    "db": {"retryable": True, "max_attempts": 2, "backoff_seconds": 1.0, "manual_review": False},
    "revert": {"retryable": False, "max_attempts": 1, "backoff_seconds": 0.0, "manual_review": True},
    "risk_blocked": {"retryable": False, "max_attempts": 1, "backoff_seconds": 0.0, "manual_review": True},
}


def normalize_error_code(value: Any) -> str:
    code = str(value or "").strip().lower()
    return code if code in ERROR_CODES else "validation"


def error_payload(
    code: Any,
    message: Any,
    *,
    trace_id: Any = None,
    details: dict[str, Any] | None = None,
    stack: ErrorStack | object = None,
) -> dict[str, Any]:
    normalized = normalize_error_code(code)
    policy = dict(RETRY_POLICIES[normalized])
    error_key = str(code or "").strip() or normalized
    definition = resolve_error_definition(error_key)
    error_stack = stack_from_legacy(stack, trace_id=trace_id) if stack is not None else ErrorStack(trace_id=trace_id)
    existing = error_stack.as_list()
    if not existing or all(item["code"] == 0 for item in existing):
        error_stack.push(
            ModuleError(
                module=definition.domain,
                code=definition.code,
                message=str(message or "").strip() or definition.key,
            )
        )
    error_stack.mask_internal()
    return {
        "error_code": normalized,
        "code": int(definition.code),
        "errorKey": definition.key,
        "message": str(message or "").strip(),
        "trace_id": str(trace_id) if trace_id else None,
        "traceId": str(trace_id) if trace_id else None,
        "errorStack": error_stack.as_list(),
        "retry": dict(policy),
        "retryable": bool(policy["retryable"]),
        "max_attempts": int(policy["max_attempts"]),
        "backoff_seconds": float(policy["backoff_seconds"]),
        "manual_review": bool(policy["manual_review"]),
        "details": details or {},
    }
