"""Pure observation-to-gate pipeline for local replay and fault injection."""

from __future__ import annotations

from typing import Any, Mapping

from core.evidence_manifest import build_evidence_manifest


def build_observation_to_gate_manifest(
    *,
    scope: Mapping[str, Any],
    generation: str,
    observation: Mapping[str, Any],
    overview: Mapping[str, Any],
    quote: Mapping[str, Any],
    pool_evidence: Mapping[str, Any],
    intent: Mapping[str, Any],
    nonce_reserved: bool = False,
    seen_idempotency_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Compose the four stages without performing any external operation."""
    for name, stage in (("observation", observation), ("overview", overview), ("quote", quote)):
        if not isinstance(stage, Mapping):
            raise ValueError(f"{name} stage must be an object")
        if stage.get("scope") is not None and dict(stage["scope"]) != dict(scope):
            raise ValueError(f"{name} scope mismatch")
        if stage.get("generation") is not None and str(stage["generation"]) != str(generation):
            raise ValueError(f"{name} generation mismatch")
    return build_evidence_manifest(
        scope=scope,
        generation=generation,
        observation=observation,
        overview=overview,
        quote=quote,
        pool_evidence=pool_evidence,
        intent=intent,
        nonce_reserved=nonce_reserved,
        seen_idempotency_keys=seen_idempotency_keys,
    )


__all__ = ["build_observation_to_gate_manifest"]
