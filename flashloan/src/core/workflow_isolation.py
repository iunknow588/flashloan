"""Checks preventing liquidation and CoW execution context cross-talk."""

from __future__ import annotations

from typing import Any, Mapping


def validate_workflow_isolation(context: Mapping[str, Any], *, expected_workflow: str) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    actual = str(context.get("workflow") or context.get("workflow_id") or "").strip().lower()
    expected = str(expected_workflow or "").strip().lower()
    if not expected:
        reasons.append("workflow_expected_missing")
    if actual != expected:
        reasons.append("workflow_mismatch")
    if not context.get("lock_key"):
        reasons.append("lock_key_missing")
    if not context.get("idempotency_key"):
        reasons.append("idempotency_key_missing")
    if context.get("queue") and str(context.get("queue")).strip().lower() not in {expected, actual}:
        reasons.append("queue_workflow_mismatch")
    elif context.get("queue") and actual and str(context.get("queue")).strip().lower() != actual:
        reasons.append("queue_workflow_mismatch")
    return not reasons, tuple(reasons)


__all__ = ["validate_workflow_isolation"]
