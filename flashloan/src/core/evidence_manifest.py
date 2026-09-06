"""Pure evidence-package manifest and dry-run gate orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from core.dry_run_gate import consume_evidence_manifest
from core.gate_decision import validate_execution_gate
from core.json_io import read_json
from core.pool_evidence_gate import validate_pool_evidence_for_gate
from core.snapshot_replay import snapshot_target_hash


MANIFEST_SCHEMA = "flashloan-evidence-package-v1"


def build_evidence_manifest(
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
    generated_at: datetime | None = None,
    max_pool_evidence_age_seconds: float | None = None,
) -> dict[str, Any]:
    """Build a replayable, fail-closed package without side effects."""
    generated = (generated_at or datetime.now(timezone.utc)).isoformat()
    stages = {"observation": dict(observation), "overview": dict(overview), "quote": dict(quote)}
    target_hash = snapshot_target_hash({"scope": dict(scope), "generation": generation, **stages})
    gate_intent = {**dict(intent), "scope": dict(scope), "target_hash": target_hash}
    allowed, reasons = validate_pool_evidence_for_gate(
        pool_evidence,
        gate_intent,
        nonce_reserved=nonce_reserved,
        seen_idempotency_keys=seen_idempotency_keys,
        max_pool_evidence_age_seconds=max_pool_evidence_age_seconds,
        now=generated_at,
    )
    return {
        "schema": MANIFEST_SCHEMA,
        "generated_at": generated,
        "scope": dict(scope),
        "generation": str(generation),
        "target_hash": target_hash,
        "stages": stages,
        "pool_evidence": dict(pool_evidence),
        "gate_decision": {"allowed": bool(allowed), "reasons": list(reasons)},
    }


def validate_evidence_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a previously built manifest without changing its gate decision."""
    if not isinstance(manifest, Mapping):
        raise ValueError("evidence manifest must be an object")
    if str(manifest.get("schema") or "").strip() != MANIFEST_SCHEMA:
        raise ValueError(f"evidence manifest schema must be {MANIFEST_SCHEMA}")
    consume_evidence_manifest(manifest)
    return dict(manifest)


def read_evidence_manifest(
    path: str | Path,
    *,
    expected_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read an evidence manifest and validate structure and optional scope."""
    payload = read_json(Path(path))
    if payload is None:
        raise ValueError(f"evidence manifest is missing or invalid: {path}")
    validated = validate_evidence_manifest(payload)
    if expected_scope is not None:
        manifest_scope = payload.get("scope")
        if not isinstance(manifest_scope, Mapping):
            raise ValueError(f"evidence manifest scope is missing: {path}")
        for key, value in dict(expected_scope).items():
            if manifest_scope.get(key) != value:
                raise ValueError(f"evidence manifest scope mismatch at {key}: {path}")
    return validated


def write_evidence_manifest(
    path: str | Path,
    manifest: Mapping[str, Any],
    *,
    expected_scope: Mapping[str, Any] | None = None,
) -> Path:
    """Persist an evidence manifest as JSON after structural validation."""
    validated = validate_evidence_manifest(manifest)
    if expected_scope is not None:
        manifest_scope = validated.get("scope")
        if not isinstance(manifest_scope, Mapping):
            raise ValueError("evidence manifest scope is missing")
        for key, value in dict(expected_scope).items():
            if manifest_scope.get(key) != value:
                raise ValueError(f"evidence manifest scope mismatch at {key}")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(validated, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return target


__all__ = [
    "MANIFEST_SCHEMA",
    "build_evidence_manifest",
    "read_evidence_manifest",
    "validate_evidence_manifest",
    "write_evidence_manifest",
]
