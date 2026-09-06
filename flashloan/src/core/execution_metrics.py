"""Domain-neutral execution metrics schema and in-memory aggregation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping


METRIC_KINDS = frozenset({"rpc", "quote", "gate", "revert", "lock", "timeout", "retry", "fallback", "stale"})
REQUIRED_FIELDS = ("kind", "name", "scope", "observed_at")


def validate_metric_event(event: Mapping[str, Any]) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    for field_name in REQUIRED_FIELDS:
        if not event.get(field_name):
            reasons.append(f"{field_name}_missing")
    if event.get("kind") not in METRIC_KINDS:
        reasons.append("kind_invalid")
    if not isinstance(event.get("scope"), Mapping):
        reasons.append("scope_invalid")
    duration = event.get("duration_seconds")
    if duration is not None:
        try:
            parsed_duration = float(duration)
        except (TypeError, ValueError):
            reasons.append("duration_seconds_invalid")
        else:
            if parsed_duration < 0 or parsed_duration != parsed_duration or parsed_duration in (float("inf"), float("-inf")):
                reasons.append("duration_seconds_invalid")
    return not reasons, tuple(reasons)


class JsonlMetricsSink:
    """Append-only metrics sink that persists validated events as JSONL."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record(self, event: Mapping[str, Any]) -> dict[str, Any]:
        stored = MetricsRegistry().record(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(stored, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
        return stored


def read_metric_events(path: str | Path) -> list[dict[str, Any]]:
    """Read all metric events from a JSONL sink, preserving file order."""
    events: list[dict[str, Any]] = []
    target = Path(path)
    if not target.exists():
        return events
    for line_number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid metric JSONL at line {line_number}") from exc
        valid, reasons = validate_metric_event(event)
        if not valid:
            raise ValueError(f"invalid metric JSONL at line {line_number}: {','.join(reasons)}")
        events.append(event)
    return events


@dataclass
class MetricsRegistry:
    """Small deterministic registry suitable for tests and adapters."""

    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, event: Mapping[str, Any]) -> dict[str, Any]:
        valid, reasons = validate_metric_event(event)
        if not valid:
            raise ValueError("invalid metric event: " + ",".join(reasons))
        stored = dict(event)
        self.events.append(stored)
        return dict(stored)

    def summary(self) -> dict[str, Any]:
        counts = Counter(str(event["kind"]) for event in self.events)
        names = Counter(str(event["name"]) for event in self.events)
        return {
            "event_count": len(self.events),
            "by_kind": dict(sorted(counts.items())),
            "by_name": dict(sorted(names.items())),
        }


__all__ = [
    "METRIC_KINDS",
    "JsonlMetricsSink",
    "MetricsRegistry",
    "REQUIRED_FIELDS",
    "read_metric_events",
    "validate_metric_event",
]
