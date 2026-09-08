from datetime import datetime, timezone

import pytest

from core.execution_metrics import (
    MetricAlertThresholds,
    evaluate_metric_alert_thresholds,
    summarize_metric_events,
)
from core.execution_metrics_delivery import (
    build_metric_alert_delivery,
    deliver_metric_alert,
    read_metric_alert_deliveries,
)


def _event(**overrides):
    return {
        "kind": "quote",
        "name": "quote_latency",
        "scope": {"chain_id": 56},
        "observed_at": "2026-09-05T00:00:00Z",
        **overrides,
    }


def test_build_metric_alert_delivery_records_evaluated_alert():
    events = [_event(duration_seconds=0.5), _event(duration_seconds=1.5)]
    summary = summarize_metric_events(events)
    alert = evaluate_metric_alert_thresholds(
        summary,
        MetricAlertThresholds(p50_seconds=0.2, p95_seconds=1.0),
    )

    record = build_metric_alert_delivery(
        summary,
        alert,
        observed_at=datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
    )

    assert record["ok"] is False
    assert record["delivery"] == "local_jsonl"
    assert [item["metric"] for item in record["violations"]] == ["p50", "p95"]
    assert record["event_count"] == 2
    assert record["latency_percentiles"]["50"] == 0.5


def test_deliver_metric_alert_writes_append_only_audit(tmp_path):
    summary = summarize_metric_events([_event(duration_seconds=0.05), _event(duration_seconds=0.06)])
    alert = evaluate_metric_alert_thresholds(summary, MetricAlertThresholds(p50_seconds=0.1))
    path = tmp_path / "alerts.jsonl"

    first = deliver_metric_alert(
        path,
        summary,
        alert,
        observed_at=datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
    )
    second = deliver_metric_alert(
        path,
        summary,
        alert,
        observed_at=datetime(2026, 9, 7, 12, 1, tzinfo=timezone.utc),
    )

    records = read_metric_alert_deliveries(path)

    assert len(records) == 2
    assert records[0]["observed_at"] != records[1]["observed_at"]
    assert first["ok"] is True
    assert second["ok"] is True


def test_deliver_metric_alert_handles_missing_path(tmp_path):
    assert read_metric_alert_deliveries(tmp_path / "missing.jsonl") == []


def test_read_metric_alert_deliveries_rejects_invalid_line(tmp_path):
    path = tmp_path / "alerts.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(ValueError):
        read_metric_alert_deliveries(path)
