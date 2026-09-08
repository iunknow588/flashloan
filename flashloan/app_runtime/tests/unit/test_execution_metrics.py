import pytest

from core.execution_metrics import (
    JsonlMetricsSink,
    MetricAlertThresholds,
    MetricsRegistry,
    evaluate_metric_alert_thresholds,
    latency_percentiles,
    read_metric_events,
    read_metric_summary,
    summarize_metric_events,
    validate_metric_event,
)


def _event(**overrides):
    return {"kind": "quote", "name": "quote_latency", "scope": {"chain_id": 56}, "observed_at": "2026-09-05T00:00:00Z", **overrides}


def test_metric_event_schema_and_summary_are_deterministic():
    registry = MetricsRegistry()
    registry.record(_event())
    registry.record(_event(kind="gate", name="gate_blocked"))

    assert registry.summary() == {"event_count": 2, "by_kind": {"gate": 1, "quote": 1}, "by_name": {"gate_blocked": 1, "quote_latency": 1}}


def test_metric_event_rejects_missing_scope_and_unknown_kind():
    valid, reasons = validate_metric_event(_event(scope=None, kind="unknown"))
    assert not valid
    assert "scope_invalid" in reasons
    assert "kind_invalid" in reasons
    with pytest.raises(ValueError):
        MetricsRegistry().record(_event(scope=None))


def test_metric_event_accepts_timeout_retry_fallback_stale_and_duration():
    registry = MetricsRegistry()
    for kind in ("timeout", "retry", "fallback", "stale"):
        registry.record(_event(kind=kind, name=f"{kind}_event", duration_seconds=0.25))

    assert registry.summary()["event_count"] == 4
    assert "timeout" in registry.summary()["by_kind"]
    assert registry.summary()["by_kind"]["retry"] == 1


def test_metric_event_rejects_invalid_duration():
    valid, reasons = validate_metric_event(_event(duration_seconds="slow"))
    assert not valid
    assert "duration_seconds_invalid" in reasons


def test_jsonl_metrics_sink_round_trips_validated_events(tmp_path):
    path = tmp_path / "metrics.jsonl"
    sink = JsonlMetricsSink(path)
    sink.record(_event(kind="rpc", name="rpc_latency", duration_seconds=0.2))
    sink.record(_event(kind="stale", name="stale_evidence", duration_seconds=0.1))

    events = read_metric_events(path)

    assert len(events) == 2
    assert events[0]["name"] == "rpc_latency"
    assert events[1]["kind"] == "stale"
    assert read_metric_events(tmp_path / "missing.jsonl") == []


def test_latency_percentiles_use_nearest_rank_and_ignore_invalid_durations():
    events = [
        _event(name="slow", duration_seconds=0.5),
        _event(name="bad", duration_seconds="invalid"),
        _event(name="fast", duration_seconds=0.1),
        _event(name="mid", duration_seconds=0.3),
        _event(name="quick", duration_seconds=0.2),
        _event(name="missing_duration"),
    ]

    metrics = latency_percentiles(events, percentiles=(50, 95, 99))

    assert metrics["sample_count"] == 4
    assert metrics["50"] == 0.2
    assert metrics["95"] == 0.5
    assert metrics["99"] == 0.5
    assert latency_percentiles([])["50"] is None


def test_metric_summary_and_jsonl_read_summary_include_percentiles(tmp_path):
    path = tmp_path / "metrics.jsonl"
    sink = JsonlMetricsSink(path)
    sink.record(_event(kind="rpc", name="rpc_latency", duration_seconds=0.12))
    sink.record(_event(kind="rpc", name="rpc_latency", duration_seconds=0.48))
    sink.record(_event(kind="gate", name="gate_blocked", duration_seconds=0.2))

    summary = summarize_metric_events(read_metric_events(path))

    assert summary["event_count"] == 3
    assert summary["by_kind"] == {"gate": 1, "rpc": 2}
    assert summary["latency_percentiles"]["sample_count"] == 3
    assert summary["latency_percentiles"]["50"] == 0.2
    assert read_metric_summary(path) == summary


def test_metric_alert_thresholds_flag_breaches_and_sample_count():
    events = [
        _event(name="slow", duration_seconds=0.1),
        _event(name="slow", duration_seconds=0.5),
        _event(name="slow", duration_seconds=1.2),
    ]
    summary = summarize_metric_events(events)
    thresholds = MetricAlertThresholds(p50_seconds=0.2, p95_seconds=1.0, sample_count_min=5)

    result = evaluate_metric_alert_thresholds(summary, thresholds)

    assert result["ok"] is False
    assert [item["metric"] for item in result["violations"]] == ["p50", "p95", "sample_count"]


def test_metric_alert_thresholds_pass_within_limits():
    events = [
        _event(name="fast", duration_seconds=0.05),
        _event(name="fast", duration_seconds=0.06),
        _event(name="fast", duration_seconds=0.07),
    ]
    summary = summarize_metric_events(events)
    thresholds = MetricAlertThresholds(p50_seconds=0.1, p95_seconds=0.1, p99_seconds=0.1)

    result = evaluate_metric_alert_thresholds(summary, thresholds)

    assert result == {"ok": True, "violations": []}
