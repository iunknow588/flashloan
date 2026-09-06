"""Small, domain-neutral API response contract builders.

Routes may enrich these payloads, but the envelope and scope error semantics
remain stable across bot, DEX and market endpoints.
"""

from __future__ import annotations

from typing import Any, Mapping


def success_response(data: Mapping[str, Any] | None = None, *, scope: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True}
    if scope is not None:
        payload["scope"] = dict(scope)
    if data:
        payload.update(dict(data))
    return payload


def error_response(code: str, message: str, *, status: int = 400, scope: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {"code": str(code), "message": str(message)},
        "status": int(status),
    }
    if scope is not None:
        payload["scope"] = dict(scope)
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
