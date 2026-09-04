"""Deterministic snapshot fingerprinting for replay and audit checks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Iterable
from pathlib import Path

from core.contracts import validate_scope_contract


DEFAULT_VOLATILE_FIELDS = frozenset(
    {"observed_at", "generated_at", "created_at", "updated_at", "request_id", "trace_id"}
)
REPLAY_SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class SnapshotReplayResult:
    target_hash: str
    gate_allowed: bool
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_hash": self.target_hash,
            "gate_allowed": self.gate_allowed,
            "reasons": list(self.reasons),
        }


def _stage_scope(stage: Mapping[str, Any]) -> tuple[Any, ...]:
    scope = stage.get("scope") if isinstance(stage.get("scope"), Mapping) else stage
    normalized = validate_scope_contract(
        market_id=scope.get("market_id"),
        chain_id=scope.get("chain_id"),
        network=scope.get("network"),
        protocol=scope.get("protocol"),
    )
    return tuple(normalized[key] for key in ("market_id", "chain_id", "network", "protocol"))


def build_workflow_replay_fixture(
    *,
    observation: Mapping[str, Any],
    overview: Mapping[str, Any],
    quote: Mapping[str, Any],
    gate_allowed: bool,
    reasons: Iterable[str] = (),
) -> dict[str, Any]:
    """Compose observation/overview/quote only when scope and generation agree."""
    stages = {"observation": observation, "overview": overview, "quote": quote}
    if any(not isinstance(stage, Mapping) for stage in stages.values()):
        raise ValueError("replay stages must be objects")
    if any(not stage.get("generation_id") for stage in stages.values()):
        raise ValueError("replay stage generation_id is required")
    generations = {str(stage["generation_id"]) for stage in stages.values()}
    if len(generations) != 1:
        raise ValueError("replay generation mismatch")
    try:
        scopes = {_stage_scope(stage) for stage in stages.values()}
    except ValueError as exc:
        raise ValueError(f"replay scope invalid: {exc}") from exc
    if len(scopes) != 1:
        raise ValueError("replay scope mismatch")
    scope_values = next(iter(scopes))
    scope = {
        "market_id": scope_values[0],
        "chain_id": scope_values[1],
        "network": scope_values[2],
        "protocol": scope_values[3],
    }
    input_snapshot = {"generation_id": next(iter(generations)), "scope": scope, "stages": stages}
    result = build_snapshot_replay_result(input_snapshot, gate_allowed=gate_allowed, reasons=reasons)
    return {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "generation_id": input_snapshot["generation_id"],
        "scope": scope,
        "stages": dict(stages),
        "replay": result.as_dict(),
    }


def write_workflow_replay_fixture(path: str | Path, fixture: Mapping[str, Any]) -> Path:
    """Persist a replay fixture after validating its required envelope."""
    validate_workflow_replay_fixture(fixture)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(fixture, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return target


def read_workflow_replay_fixture(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    try:
        fixture = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid replay fixture: {target}") from exc
    validate_workflow_replay_fixture(fixture)
    return fixture


def validate_workflow_replay_fixture(fixture: Mapping[str, Any]) -> bool:
    if not isinstance(fixture, Mapping) or fixture.get("schema_version") != REPLAY_SCHEMA_VERSION:
        raise ValueError("unsupported replay fixture schema")
    stages = fixture.get("stages")
    replay = fixture.get("replay")
    if not isinstance(stages, Mapping) or set(stages) != {"observation", "overview", "quote"}:
        raise ValueError("replay fixture stages are incomplete")
    if not isinstance(replay, Mapping) or not str(replay.get("target_hash") or "").strip():
        raise ValueError("replay fixture result is incomplete")
    scope = fixture.get("scope")
    if not isinstance(scope, Mapping):
        raise ValueError("replay fixture scope is missing")
    try:
        normalized_scope = validate_scope_contract(**{key: scope.get(key) for key in ("market_id", "chain_id", "network", "protocol")})
    except ValueError as exc:
        raise ValueError(f"replay fixture scope invalid: {exc}") from exc
    if tuple(normalized_scope[key] for key in ("market_id", "chain_id", "network", "protocol")) != _stage_scope(next(iter(stages.values()))):
        raise ValueError("replay fixture scope mismatch")
    snapshot = {"generation_id": fixture.get("generation_id"), "scope": normalized_scope, "stages": dict(stages)}
    verify_snapshot_replay(snapshot, str(replay["target_hash"]))
    return True


def canonical_snapshot(
    value: Any,
    *,
    volatile_fields: Iterable[str] = DEFAULT_VOLATILE_FIELDS,
) -> Any:
    """Return a JSON-compatible snapshot with volatile fields removed."""
    volatile = {str(field) for field in volatile_fields}
    if isinstance(value, Mapping):
        return {
            str(key): canonical_snapshot(item, volatile_fields=volatile)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in volatile
        }
    if isinstance(value, (list, tuple)):
        return [canonical_snapshot(item, volatile_fields=volatile) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"snapshot value is not JSON-compatible: {type(value).__name__}")


def snapshot_target_hash(
    snapshot: Mapping[str, Any],
    *,
    volatile_fields: Iterable[str] = DEFAULT_VOLATILE_FIELDS,
) -> str:
    """Return a stable sha256 fingerprint for replayable business input."""
    canonical = canonical_snapshot(snapshot, volatile_fields=volatile_fields)
    encoded = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_snapshot_replay(
    snapshot: Mapping[str, Any],
    expected_target_hash: str,
    *,
    volatile_fields: Iterable[str] = DEFAULT_VOLATILE_FIELDS,
) -> bool:
    """Verify that replay input produces the expected target fingerprint."""
    actual = snapshot_target_hash(snapshot, volatile_fields=volatile_fields)
    if actual != str(expected_target_hash).strip():
        raise ValueError(f"snapshot target_hash mismatch: {actual} != {expected_target_hash}")
    return True


def build_snapshot_replay_result(
    snapshot: Mapping[str, Any],
    *,
    gate_allowed: bool,
    reasons: Iterable[str] = (),
    volatile_fields: Iterable[str] = DEFAULT_VOLATILE_FIELDS,
) -> SnapshotReplayResult:
    """Build the stable replay record without deciding the gate itself."""
    return SnapshotReplayResult(
        target_hash=snapshot_target_hash(snapshot, volatile_fields=volatile_fields),
        gate_allowed=bool(gate_allowed),
        reasons=tuple(str(reason).strip() for reason in reasons if str(reason).strip()),
    )


__all__ = [
    "DEFAULT_VOLATILE_FIELDS",
    "REPLAY_SCHEMA_VERSION",
    "SnapshotReplayResult",
    "build_snapshot_replay_result",
    "build_workflow_replay_fixture",
    "canonical_snapshot",
    "snapshot_target_hash",
    "read_workflow_replay_fixture",
    "validate_workflow_replay_fixture",
    "verify_snapshot_replay",
    "write_workflow_replay_fixture",
]
