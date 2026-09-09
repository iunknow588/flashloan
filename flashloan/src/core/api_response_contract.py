"""Small, domain-neutral API response contract builders.

Routes may enrich these payloads, but the envelope and scope error semantics
remain stable across bot, DEX and market endpoints.
"""

from __future__ import annotations

from typing import Any, Mapping

from core.error_registry import resolve_error_definition
from core.error_stack import ErrorStack, ModuleError, stack_from_legacy


def _stack_dict(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict) and "errorStack" in value:
        return dict(value)
    if isinstance(value, ErrorStack):
        return value.mask_internal().as_dict()
    return stack_from_legacy(value).as_dict()


def _empty_error_stack() -> list[dict[str, Any]]:
    stack = ErrorStack()
    stack.mark_ok(ModuleError(module="CORE", code=0, message="OK"))
    return stack.as_list()


def _stack_rows(stack_dict: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(stack_dict, dict):
        return []
    rows = stack_dict.get("errorStack")
    return rows if isinstance(rows, list) else []


def _has_error(rows: list[dict[str, Any]]) -> bool:
    return any(isinstance(row, dict) and int(row.get("code") or 0) != 0 for row in rows)


def _stack_from_code(code: str, message: str) -> list[dict[str, Any]]:
    definition = resolve_error_definition(str(code or "").strip() or "CORE.INTERNAL")
    stack = ErrorStack()
    stack.push(ModuleError(module=definition.domain, code=definition.code, message=str(message or "").strip() or definition.key))
    return stack.as_list()


def success_response(data: Mapping[str, Any] | None = None, *, scope: Mapping[str, Any] | None = None, stack: object = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True}
    if scope is not None:
        payload["scope"] = dict(scope)
    stack_dict = _stack_dict(stack)
    rows = _stack_rows(stack_dict)
    if _has_error(rows):
        payload["errorStack"] = rows
    else:
        # This is the API/module boundary.  A child OK stamp is intentionally
        # replaced by the boundary's own single OK element.
        payload["errorStack"] = _empty_error_stack()
    if data:
        payload.update(dict(data))
    return payload


def error_response(code: str, message: str, *, status: int = 400, scope: Mapping[str, Any] | None = None, stack: object = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {"code": str(code), "message": str(message)},
        "status": int(status),
    }
    if scope is not None:
        payload["scope"] = dict(scope)
    stack_dict = _stack_dict(stack)
    rows = _stack_rows(stack_dict)
    if _has_error(rows):
        payload["errorStack"] = rows
    else:
        # A failed API response always needs its own failure body.  An empty
        # or child-OK stack cannot explain this boundary failure.
        payload["errorStack"] = _stack_from_code(code, message)
    return payload


def missing_scope_response(message: str = "scope is required") -> dict[str, Any]:
    return error_response("missing_scope", message, status=400)


def wrong_scope_response(message: str = "scope does not match the requested resource", *, scope: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return error_response("wrong_scope", message, status=409, scope=scope)


__all__ = [
    "error_response",
    "missing_scope_response",
    "success_response",
    "wrong_scope_response",
]
