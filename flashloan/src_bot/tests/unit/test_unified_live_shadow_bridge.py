from datetime import datetime, timezone

from tools import run_unified_live_shadow_bridge as bridge


def test_build_shadow_input_from_extremes_marks_live_signal():
    observed_at = datetime.now(timezone.utc).isoformat()
    payload = bridge.build_shadow_input_from_extremes(
        {
            "observed_at": observed_at,
            "window_seconds": 1.0,
            "sample_count": 3,
            "price_source": "ws",
            "top": [{"symbol": "BTCUSDT", "change_percent": 1.2}],
            "bottom": [{"symbol": "ETHUSDT", "change_percent": -1.1}],
        },
        side_limit=5,
    )

    assert payload["evidenceSemantics"] == {"liveSignalOnly": True, "historicalForkOnly": False}
    assert payload["observedAt"] == observed_at
    assert payload["market_state"]["network"] == "avalanche"
    assert payload["market_state"]["top"][0]["base_symbol"] == "BTC"
    assert payload["market_state"]["bottom"][0]["base_symbol"] == "ETH"
    assert payload["source"]["freshnessPassed"] is True


def test_build_shadow_input_from_extremes_requires_candidate_ready_for_live_signal():
    payload = bridge.build_shadow_input_from_extremes(
        {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "top": [],
            "bottom": [],
        },
        side_limit=5,
    )

    assert payload["source"]["candidateReady"] is False
    assert payload["source"]["freshnessPassed"] is False
    assert payload["source"]["freshnessReason"] == "latest_extremes_missing_candidate"


def test_build_shadow_input_from_extremes_rejects_stale_snapshot():
    payload = bridge.build_shadow_input_from_extremes(
        {
            "observed_at": "2020-01-01T00:00:00+00:00",
            "top": [{"symbol": "BTCUSDT", "change_percent": 1.2}],
            "bottom": [{"symbol": "ETHUSDT", "change_percent": -1.1}],
        },
        side_limit=5,
        max_age_seconds=5,
    )

    assert payload["evidenceSemantics"]["liveSignalOnly"] is False
    assert payload["source"]["freshnessPassed"] is False
    assert payload["source"]["freshnessReason"] == "latest_extremes_stale"


def test_build_shadow_input_records_custom_extremes_source_path(tmp_path):
    source_path = tmp_path / "custom_extremes.json"
    payload = bridge.build_shadow_input_from_extremes(
        {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "top": [],
            "bottom": [],
        },
        source_path=source_path,
    )

    assert payload["source"]["path"] == str(source_path)


def test_bridge_main_forces_broadcast_off_and_calls_shadow_runner(monkeypatch, tmp_path):
    extremes_path = tmp_path / "latest_extremes.json"
    input_path = tmp_path / "unified_live_shadow_input.json"
    extremes_path.write_text(
        (
            '{"observed_at":"'
            + datetime.now(timezone.utc).isoformat()
            + '","top":[],"bottom":[]}'
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_run_shadow_once(args):
        calls.append(args)
        return {
            "ok": True,
            "runId": "run-1",
            "report": "report.json",
            "positiveSignalCount": 0,
        }

    monkeypatch.setattr(bridge.run_unified_live_shadow, "run_shadow_once", fake_run_shadow_once)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_unified_live_shadow_bridge.py",
            "--extremes",
            str(extremes_path),
            "--input",
            str(input_path),
            "--iterations",
            "1",
        ],
    )
    monkeypatch.setenv("UNIFIED_EXECUTOR_BROADCAST_ENABLED", "true")
    monkeypatch.setenv("TRIANGULAR_DIRECT_BROADCAST_ENABLED", "true")

    assert bridge.main() == 0
    assert calls and calls[0].input == input_path
    assert input_path.exists()
    assert bridge.os.environ["UNIFIED_EXECUTOR_BROADCAST_ENABLED"] == "false"
    assert bridge.os.environ["TRIANGULAR_DIRECT_BROADCAST_ENABLED"] == "false"


def test_bridge_report_tracks_incremental_health_and_hash(tmp_path):
    child_index = tmp_path / "child_reports.jsonl"
    report = bridge._bridge_report(
        run_id="bridge-1",
        started_at="2026-08-13T00:00:00+00:00",
        stopped_at="2026-08-13T00:00:10+00:00",
        input_path=tmp_path / "input.json",
        extremes_path=tmp_path / "extremes.json",
        child_index_path=child_index,
        iterations_requested=10,
        iterations_completed=4,
        fresh_count=3,
        diagnostic_count=1,
        positive_signal_count=0,
        child_report_count=4,
        eligible_child_report_count=0,
        invalid_child_report_count=4,
        last_child_report="child-report.json",
        interval_ms=10_000,
        max_extremes_age_seconds=5,
        stale_alert_seconds=600,
        freshness_reason_counts={"fresh": 3, "latest_extremes_stale": 1},
    )

    assert report["freshnessRate"] == 0.75
    assert report["health"]["interrupted"] is True
    assert report["health"]["pollingFreshnessRatioWarning"] == "bridge_interval_exceeds_extremes_freshness_window"
    assert report["manualReviewThresholdBps"] == 5000
    assert report["manualReviewThresholdRationale"]
    assert report["manualReviewThresholdAdjustmentHistory"] == []
    assert report["polling"]["intervalWithinFreshnessWindow"] is False
    assert report["polling"]["staleSnapshotCount"] == 1
    assert report["health"]["staleSnapshotAlert"] == ""
    assert report["polling"]["freshnessReasonCounts"]["latest_extremes_stale"] == 1
    assert report["schemaValidation"]["ok"] is True
    assert report["evidenceSemantics"]["evidenceEligible"] is False
    assert report["windowComplete"] is False
    assert report["runClassification"] == "bridge_window_incomplete"
    assert report["reportHash"].startswith("sha256:")


def test_bridge_report_flags_long_stale_extremes_window(tmp_path):
    child_index = tmp_path / "child_reports.jsonl"
    report = bridge._bridge_report(
        run_id="bridge-stale-alert",
        started_at="2026-08-13T00:00:00+00:00",
        stopped_at="2026-08-13T00:10:00+00:00",
        input_path=tmp_path / "input.json",
        extremes_path=tmp_path / "extremes.json",
        child_index_path=child_index,
        iterations_requested=3,
        iterations_completed=3,
        fresh_count=0,
        diagnostic_count=3,
        positive_signal_count=0,
        child_report_count=3,
        eligible_child_report_count=0,
        invalid_child_report_count=3,
        last_child_report="child-report.json",
        interval_ms=1_000,
        max_extremes_age_seconds=5,
        stale_alert_seconds=3,
        freshness_reason_counts={"latest_extremes_stale": 2, "observed_at_missing_or_invalid": 1},
    )

    assert report["polling"]["staleSnapshotCount"] == 3
    assert report["polling"]["staleAlertIterationThreshold"] == 3
    assert report["health"]["staleSnapshotAlert"] == "bridge_no_fresh_extremes_for_stale_alert_window"
    assert report["evidenceSemantics"]["evidenceEligible"] is False


def test_bridge_report_requires_all_child_reports_to_be_eligible(tmp_path):
    child_index = tmp_path / "child_reports.jsonl"
    report = bridge._bridge_report(
        run_id="bridge-valid",
        started_at="2026-08-13T00:00:00+00:00",
        stopped_at="2026-08-13T00:00:10+00:00",
        input_path=tmp_path / "input.json",
        extremes_path=tmp_path / "extremes.json",
        child_index_path=child_index,
        iterations_requested=2,
        iterations_completed=2,
        fresh_count=2,
        diagnostic_count=0,
        positive_signal_count=1,
        child_report_count=2,
        eligible_child_report_count=2,
        invalid_child_report_count=0,
        last_child_report="child-report.json",
    )

    assert report["evidenceSemantics"]["evidenceEligible"] is True
    assert report["windowComplete"] is True
    assert report["runClassification"] == "live_shadow_bridge_window"
    assert report["zeroSignalReason"] == ""


def test_bridge_report_marks_complete_window_with_invalid_children_ineligible(tmp_path):
    child_index = tmp_path / "child_reports.jsonl"
    report = bridge._bridge_report(
        run_id="bridge-invalid",
        started_at="2026-08-13T00:00:00+00:00",
        stopped_at="2026-08-13T00:00:10+00:00",
        input_path=tmp_path / "input.json",
        extremes_path=tmp_path / "extremes.json",
        child_index_path=child_index,
        iterations_requested=2,
        iterations_completed=2,
        fresh_count=2,
        diagnostic_count=0,
        positive_signal_count=1,
        child_report_count=2,
        eligible_child_report_count=1,
        invalid_child_report_count=1,
        last_child_report="child-report.json",
    )

    assert report["windowComplete"] is True
    assert report["evidenceSemantics"]["evidenceEligible"] is False
    assert report["runClassification"] == "bridge_window_contains_invalid_child_reports"
    assert report["zeroSignalReason"] == "bridge_contains_invalid_child_reports"


def test_bridge_main_records_stale_snapshot_as_diagnostic_reason(monkeypatch, tmp_path):
    extremes_path = tmp_path / "latest_extremes.json"
    input_path = tmp_path / "unified_live_shadow_input.json"
    evidence_root = tmp_path / "evidence"
    extremes_path.write_text(
        (
            '{"observed_at":"2020-01-01T00:00:00+00:00",'
            '"top":[{"symbol":"BTCUSDT","change_percent":1.2}],'
            '"bottom":[{"symbol":"ETHUSDT","change_percent":-1.1}]}'
        ),
        encoding="utf-8",
    )

    def fake_run_shadow_once(_args):
        return {
            "ok": True,
            "runId": "child-1",
            "report": "child-report.json",
            "positiveSignalCount": 0,
            "evidenceSemantics": {"evidenceEligible": False},
            "validSampleCount": 0,
            "invalidSampleCount": 1,
        }

    monkeypatch.setattr(bridge.run_unified_live_shadow, "run_shadow_once", fake_run_shadow_once)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_unified_live_shadow_bridge.py",
            "--extremes",
            str(extremes_path),
            "--input",
            str(input_path),
            "--evidence-root",
            str(evidence_root),
            "--iterations",
            "1",
            "--max-extremes-age-seconds",
            "5",
        ],
    )

    assert bridge.main() == 0
    reports = list(evidence_root.glob("*_unified-live-bridge/report.json"))
    assert reports
    report = bridge.json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["diagnosticSnapshotCount"] == 1
    assert report["polling"]["freshnessReasonCounts"] == {"latest_extremes_stale": 1}
    assert report["evidenceSemantics"]["evidenceEligible"] is False
