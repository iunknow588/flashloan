import pytest

from core.execution_metrics import JsonlMetricsSink, MetricsRegistry, read_metric_events, validate_metric_event


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
