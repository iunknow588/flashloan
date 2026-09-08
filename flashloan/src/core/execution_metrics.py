"""Domain-neutral execution metrics schema and in-memory aggregation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import math
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


def latency_percentiles(events: list[Mapping[str, Any]], percentiles: tuple[float, ...] = (50, 95, 99)) -> dict[str, Any]:
    """Return nearest-rank latency percentiles for events with valid durations."""
    durations = []
    for event in events:
        raw = event.get("duration_seconds")
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value >= 0 and math.isfinite(value):
            durations.append(value)
    durations.sort()
    result: dict[str, Any] = {}
    for percentile in percentiles:
        key = str(int(percentile))
        if not durations:
            result[key] = None
            continue
        index = min(len(durations) - 1, max(0, math.ceil(percentile / 100 * len(durations)) - 1))
        result[key] = durations[index]
    result["sample_count"] = len(durations)
    return result


def summarize_metric_events(events: list[Mapping[str, Any]], percentiles: tuple[float, ...] = (50, 95, 99)) -> dict[str, Any]:
    """Summarize validated metric events with counts and latency percentiles."""
    counts = Counter(str(event["kind"]) for event in events)
    names = Counter(str(event["name"]) for event in events)
    return {
        "event_count": len(events),
        "by_kind": dict(sorted(counts.items())),
        "by_name": dict(sorted(names.items())),
        "latency_percentiles": latency_percentiles(events, percentiles=percentiles),
    }


def read_metric_summary(path: str | Path, percentiles: tuple[float, ...] = (50, 95, 99)) -> dict[str, Any]:
    """Read a JSONL metrics sink and summarize its validated events."""
    return summarize_metric_events(read_metric_events(path), percentiles=percentiles)


@dataclass(frozen=True)
class MetricAlertThresholds:
    """Latency threshold limits in seconds for live metrics alerting."""

    p50_seconds: float | None = None
    p95_seconds: float | None = None
    p99_seconds: float | None = None
    sample_count_min: int = 0


def evaluate_metric_alert_thresholds(
    summary: Mapping[str, Any],
    thresholds: MetricAlertThresholds,
) -> dict[str, Any]:
    """Compare a metric summary to alert thresholds and return violation rows."""
    latency = summary.get("latency_percentiles") or {}
    sample_count = int(latency.get("sample_count") or 0)
    checks = (
        (50, thresholds.p50_seconds),
        (95, thresholds.p95_seconds),
        (99, thresholds.p99_seconds),
    )
    violations: list[dict[str, Any]] = []
    for metric, threshold in checks:
        if threshold is None:
            continue
        value = latency.get(str(metric))
        if value is not None and float(value) > float(threshold):
            violations.append({"metric": f"p{metric}", "threshold": float(threshold), "value": float(value)})
    if sample_count < thresholds.sample_count_min:
        violations.append(
            {
                "metric": "sample_count",
                "threshold": thresholds.sample_count_min,
                "value": sample_count,
            }
        )
    return {
        "ok": not violations,
        "violations": violations,
    }


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

    def latency_summary(self, percentiles: tuple[float, ...] = (50, 95, 99)) -> dict[str, Any]:
        return latency_percentiles(self.events, percentiles=percentiles)


__all__ = [
    "METRIC_KINDS",
    "MetricAlertThresholds",
    "JsonlMetricsSink",
    "MetricsRegistry",
    "REQUIRED_FIELDS",
    "evaluate_metric_alert_thresholds",
    "latency_percentiles",
    "read_metric_summary",
    "read_metric_events",
    "summarize_metric_events",
    "validate_metric_event",
]
