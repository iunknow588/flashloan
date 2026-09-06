"""Pure bridge between pool evidence and the pre-broadcast gate."""

from __future__ import annotations

from typing import Any, Mapping

from core.gate_decision import validate_execution_gate
from core.pool_evidence_contract import POOL_EVIDENCE_FIELDS, validate_pool_evidence_contract
from core.pool_evidence_freshness import validate_pool_evidence_freshness


def validate_pool_evidence_for_gate(
    evidence: Mapping[str, Any],
    intent: Mapping[str, Any],
    *,
    nonce_reserved: bool = False,
    seen_idempotency_keys: set[str] | None = None,
    max_pool_evidence_age_seconds: float | None = None,
    now: Any = None,
) -> tuple[bool, tuple[str, ...]]:
    """Validate evidence and intent before constructing a submission decision.

    This function only validates caller-provided facts. It never upgrades a
    ``requires_live_quoter`` or other diagnostic state into permission.
    """
    valid, reasons = validate_pool_evidence_contract(evidence)
    if not valid:
        return False, tuple(f"pool_evidence:{reason}" for reason in reasons)
    blockers: list[str] = []
    fresh, freshness_reasons = validate_pool_evidence_freshness(
        evidence,
        max_age_seconds=max_pool_evidence_age_seconds,
        now=now,
    )
    if not fresh:
        blockers.extend(f"pool_evidence:{reason}" for reason in freshness_reasons)
    for field in POOL_EVIDENCE_FIELDS:
        item = evidence[field]
        if item.get("allowed") is not True:
            blockers.extend(f"{field}:{reason}" for reason in item.get("blocked_reasons") or ["not_allowed"])
    gate_allowed, gate_reasons = validate_execution_gate(
        intent,
        nonce_reserved=nonce_reserved,
        seen_idempotency_keys=seen_idempotency_keys,
    )
    blockers.extend(f"gate:{reason}" for reason in gate_reasons)
    return not blockers and gate_allowed, tuple(blockers)


__all__ = ["validate_pool_evidence_for_gate"]
