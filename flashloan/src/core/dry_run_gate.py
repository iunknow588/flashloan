"""Pure dry-run consumption of an evidence manifest before execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class DryRunDecision:
    manifest_schema: str
    target_hash: str
    allowed: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest_schema": self.manifest_schema,
            "target_hash": self.target_hash,
            "allowed": self.allowed,
            "reasons": list(self.reasons),
        }


def consume_evidence_manifest(
    manifest: Mapping[str, Any],
    *,
    extra_blockers: Iterable[str] = (),
) -> DryRunDecision:
    """Return the auditable dry-run decision for a previously built manifest.

    The plain function only consumes caller-provided facts and never performs
    network, database, nonce, or broadcast operations.
    """
    if not isinstance(manifest, Mapping):
        raise ValueError("evidence manifest must be an object")
    schema = str(manifest.get("schema") or "").strip()
    if not schema:
        raise ValueError("evidence manifest schema is missing")
    target_hash = str(manifest.get("target_hash") or "").strip()
    if not target_hash:
        raise ValueError("evidence manifest target_hash is missing")
    gate = manifest.get("gate_decision")
    if not isinstance(gate, Mapping):
        raise ValueError("evidence manifest gate_decision is missing")
    base_reasons = list(gate.get("reasons") or [])
    reasons: list[str] = []
    for reason in [*base_reasons, *extra_blockers]:
        text = str(reason).strip()
        if text and text not in reasons:
            reasons.append(text)
    allowed = bool(gate.get("allowed")) and not reasons
    return DryRunDecision(
        manifest_schema=schema,
        target_hash=target_hash,
        allowed=allowed,
        reasons=tuple(reasons),
    )


__all__ = ["DryRunDecision", "consume_evidence_manifest"]
