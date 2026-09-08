"""Local execution metric alert delivery helpers.

This module keeps delivery local and audit-friendly: evaluated alerts are
written to an append-only JSONL file. Real external delivery belongs behind a
separate adapter and is intentionally not wired here.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping


def build_metric_alert_delivery(
    summary: Mapping[str, Any],
    alert: Mapping[str, Any],
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Return a structured alert delivery record based on an evaluated summary."""
    timestamp = observed_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    percentiles = summary.get("latency_percentiles") or {}
    return {
        "observed_at": timestamp.astimezone(timezone.utc).isoformat(),
        "ok": bool(alert.get("ok")),
        "violations": list(alert.get("violations") or []),
        "event_count": int(summary.get("event_count") or 0),
        "latency_percentiles": {
            str(percentile): percentiles.get(str(percentile))
            for percentile in (50, 95, 99)
        },
        "delivery": "local_jsonl",
    }


def deliver_metric_alert(
    delivery_path: str | Path,
    summary: Mapping[str, Any],
    alert: Mapping[str, Any],
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Append an evaluated metric alert to a local JSONL audit file."""
    record = build_metric_alert_delivery(summary, alert, observed_at=observed_at)
    target = Path(delivery_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
    return record


def read_metric_alert_deliveries(path: str | Path) -> list[dict[str, Any]]:
    """Read all alert delivery records from a local JSONL audit file."""
    target = Path(path)
    records: list[dict[str, Any]] = []
    if not target.exists():
        return records
    for line_number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid metric alert delivery at line {line_number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"invalid metric alert delivery at line {line_number}: expected object")
        records.append(record)
    return records


__all__ = [
    "build_metric_alert_delivery",
    "deliver_metric_alert",
    "read_metric_alert_deliveries",
]
