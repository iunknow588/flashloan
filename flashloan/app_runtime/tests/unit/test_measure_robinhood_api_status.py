from __future__ import annotations

import importlib.util
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[4] / "scripts" / "measure_robinhood_api_status.py"
SPEC = importlib.util.spec_from_file_location("measure_robinhood_api_status", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
measure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measure)


def test_percentile_and_endpoint_summary():
    assert measure.percentile([], 0.95) is None
    assert measure.percentile([10.0], 0.95) == 10.0
    assert measure.percentile([10.0, 20.0, 30.0, 40.0], 0.95) == 38.5

    summary = measure.endpoint_summary(
        [
            {"status": 200, "elapsedMs": 10.0, "bodyBytes": 100, "error": None},
            {
                "status": 200,
                "elapsedMs": 20.0,
                "bodyBytes": 40,
                "error": None,
                "parseError": "invalid json",
            },
            {"status": 503, "elapsedMs": 30.0, "bodyBytes": 50, "error": "HTTP 503"},
        ],
        expected_statuses={200},
    )

    assert summary["sampleCount"] == 3
    assert summary["successCount"] == 1
    assert summary["failureCount"] == 2
    assert summary["unexpected5xxCount"] == 1
    assert summary["errorRate"] == 0.666667
    assert summary["latencyMs"]["p50"] == 20.0


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            payload = {"status": "ok"}
            self.send_response(200)
        else:
            payload = {"ok": True, "pid": 1234}
            self.send_response(418)
        body = json.dumps(payload).encode("utf-8")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def test_request_once_records_success_and_http_error_without_body_leakage():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        success = measure.request_once(f"{base_url}/healthz", timeout=2)
        error = measure.request_once(f"{base_url}/missing", timeout=2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert success["status"] == 200
    assert success["bodyBytes"] > 0
    assert success["bodySha256"]
    assert success["payload"]["status"] == "ok"
    assert success["error"] is None
    assert error["status"] == 418
    assert error["payload"]["ok"] is True
    assert "payloadForEvaluation" not in measure.public_sample(error)


def test_process_marker_matches_command_line():
    identity = {
        "status": "resolved",
        "pid": 1,
        "commandLine": r"C:\Python\python.exe E:\repo\flashloan\app_runtime\run.py",
    }

    assert measure.process_marker_matches(identity, "flashloan/app_runtime/run.py")
    assert not measure.process_marker_matches(identity, "other-service.py")


def test_read_process_metrics_for_current_process():
    metrics = measure.read_process_metrics(os.getpid())

    assert "error" not in metrics
    assert metrics["pid"] == os.getpid()
    assert metrics["rssBytes"] > 0
    assert metrics["threadCount"] > 0


def test_resource_stability_gate_exposes_aggregate_pass_flag():
    passed = measure.resource_stability_gate(
        applicable=True,
        duration_passed=True,
        growth_passed=True,
        required_duration_seconds=300.0,
        observed_duration_seconds=300.2,
        rss_delta_mb=-4.0,
        max_rss_growth_mb=64.0,
        thread_delta=0,
        max_thread_growth=2,
    )
    failed = measure.resource_stability_gate(
        applicable=True,
        duration_passed=True,
        growth_passed=False,
        required_duration_seconds=300.0,
        observed_duration_seconds=300.2,
        rss_delta_mb=65.0,
        max_rss_growth_mb=64.0,
        thread_delta=0,
        max_thread_growth=2,
    )
    not_requested = measure.resource_stability_gate(
        applicable=False,
        duration_passed=False,
        growth_passed=False,
        required_duration_seconds=300.0,
        observed_duration_seconds=None,
        rss_delta_mb=None,
        max_rss_growth_mb=64.0,
        thread_delta=None,
        max_thread_growth=2,
    )

    assert passed["passed"] is True
    assert failed["passed"] is False
    assert not_requested["passed"] is True
