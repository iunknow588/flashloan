"""Validation for the pool-evidence contract emitted by core stores."""

from __future__ import annotations

from typing import Any, Mapping


POOL_EVIDENCE_FIELDS = ("liquidity", "marketability", "profitability", "executability")


def validate_pool_evidence_contract(payload: Mapping[str, Any]) -> tuple[bool, tuple[str, ...]]:
    """Return whether a pool evidence payload is structurally safe to consume.

    This is intentionally a pure schema check. It does not infer profitability
    or convert cached evidence into permission to broadcast.
    """
    reasons: list[str] = []
    if not isinstance(payload, Mapping):
        return False, ("payload_not_object",)
    for field in POOL_EVIDENCE_FIELDS:
        value = payload.get(field)
        if not isinstance(value, Mapping):
            reasons.append(f"{field}_missing_or_not_object")
            continue
        status = str(value.get("status") or "").strip()
        if not status:
            reasons.append(f"{field}_status_missing")
        if not isinstance(value.get("allowed"), bool):
            reasons.append(f"{field}_allowed_not_boolean")
        blocked = value.get("blocked_reasons")
        if not isinstance(blocked, list) or not all(isinstance(item, str) and item.strip() for item in blocked):
            reasons.append(f"{field}_blocked_reasons_invalid")
        if value.get("allowed") is True and blocked:
            reasons.append(f"{field}_allowed_with_blocked_reasons")
        if value.get("allowed") is False and not blocked:
            reasons.append(f"{field}_blocked_without_reason")
    return not reasons, tuple(reasons)


__all__ = ["POOL_EVIDENCE_FIELDS", "validate_pool_evidence_contract"]
