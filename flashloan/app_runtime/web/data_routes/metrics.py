"""Read-only execution metrics JSONL sink query route."""

from __future__ import annotations

import os
from pathlib import Path

from flask import jsonify, request

from core.execution_metrics import (
    MetricAlertThresholds,
    evaluate_metric_alert_thresholds,
    read_metric_events,
    summarize_metric_events,
)
from core.execution_metrics_delivery import deliver_metric_alert


def live_metrics_sink_path(env: object | None = None) -> Path:
    """Return the configured live metrics JSONL path or the default app_runtime path."""
    values = {**os.environ, **env} if isinstance(env, dict) else dict(os.environ)
    raw = str(
        values.get("EXECUTION_METRICS_PATH")
        or values.get("LIVE_METRICS_SINK_PATH")
        or ""
    ).strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2] / "runtime" / "metrics" / "execution.jsonl"


def _query_float(name: str) -> float | None:
    raw = request.args.get(name)
    if raw is None or not raw.strip():
        return None
    value = float(raw)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _query_int(name: str) -> int:
    raw = request.args.get(name)
    if raw is None or not raw.strip():
        return 0
    value = int(raw)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def register_metrics_routes(app, panel) -> None:
    def alert_delivery_path(env: object | None = None) -> Path:
        values = {**os.environ, **env} if isinstance(env, dict) else dict(os.environ)
        raw = str(values.get("METRICS_ALERT_DELIVERY_PATH") or "").strip()
        if raw:
            return Path(raw)
        return Path(__file__).resolve().parents[2] / "runtime" / "metrics" / "alert_delivery.jsonl"

    @app.post("/api/metrics/deliver-alert")
    def metrics_deliver_alert():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "body must be a JSON object", "code": "metrics_payload_invalid"}), 400
        path = live_metrics_sink_path(payload)
        try:
            events = read_metric_events(path)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc), "code": "metrics_invalid"}), 400
        summary = summarize_metric_events(events)
        try:
            thresholds = MetricAlertThresholds(
                p50_seconds=_json_float(payload, "p50_seconds"),
                p95_seconds=_json_float(payload, "p95_seconds"),
                p99_seconds=_json_float(payload, "p99_seconds"),
                sample_count_min=_json_int(payload, "sample_count_min"),
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc), "code": "metrics_threshold_invalid"}), 400
        alert = evaluate_metric_alert_thresholds(summary, thresholds)
        delivery_path = alert_delivery_path(payload)
        if "delivery_path" in payload:
            delivery_path = Path(str(payload["delivery_path"]))
        delivery = deliver_metric_alert(delivery_path, summary, alert)
        summary["ok"] = True
        summary["path"] = str(path)
        summary["alert"] = alert
        summary["delivery"] = delivery
        return jsonify(summary)

    @app.get("/api/metrics/summary")
    def metrics_summary():
        path = live_metrics_sink_path()
        try:
            events = read_metric_events(path)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc), "code": "metrics_invalid"}), 400
        summary = summarize_metric_events(events)
        summary["ok"] = True
        summary["path"] = str(path)
        try:
            thresholds = MetricAlertThresholds(
                p50_seconds=_query_float("alert_p50_seconds"),
                p95_seconds=_query_float("alert_p95_seconds"),
                p99_seconds=_query_float("alert_p99_seconds"),
                sample_count_min=_query_int("alert_sample_count_min"),
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc), "code": "metrics_threshold_invalid"}), 400
        if any(value is not None for value in (thresholds.p50_seconds, thresholds.p95_seconds, thresholds.p99_seconds)) or thresholds.sample_count_min:
            summary["alert"] = evaluate_metric_alert_thresholds(summary, thresholds)
        return jsonify(summary)


def _json_float(payload: dict, name: str) -> float | None:
    value = payload.get(name)
    if value is None or str(value).strip() == "":
        return None
    parsed = float(value)
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed


def _json_int(payload: dict, name: str) -> int:
    value = payload.get(name)
    if value is None or str(value).strip() == "":
        return 0
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed
