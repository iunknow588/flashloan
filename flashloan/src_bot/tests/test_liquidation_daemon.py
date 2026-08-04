import json
from pathlib import Path

from runtime import liquidation_daemon
from runtime.liquidation_daemon import market_status_payload, prepare_market_runtime, read_daemon_status


def test_read_daemon_status_returns_empty_for_missing_file(tmp_path):
    assert read_daemon_status(tmp_path / "missing.json") == {}


def test_read_daemon_status_reads_json_object(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"state": "running", "pid": 123}), encoding="utf-8")

    assert read_daemon_status(path) == {"state": "running", "pid": 123}


def test_read_daemon_status_marks_missing_process_stale(monkeypatch, tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"state": "running", "pid": 999999999, "updated_at": 1}), encoding="utf-8")
    monkeypatch.setattr(liquidation_daemon, "STATUS_PATH", path)

    status = read_daemon_status()

    assert status["state"] == "stale"
    assert status["stale"] is True
    assert status["stale_reason"] == "daemon process is not running"


def test_read_daemon_status_marks_old_heartbeat_stale(monkeypatch, tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"state": "degraded", "pid": 123, "updated_at": 10.0}), encoding="utf-8")
    monkeypatch.setattr(liquidation_daemon, "STATUS_PATH", path)
    monkeypatch.setattr(liquidation_daemon, "_process_exists", lambda _pid: True)
    monkeypatch.setattr(liquidation_daemon.time, "time", lambda: 100.0)
    monkeypatch.setenv("LIQUIDATION_DAEMON_STALE_SECONDS", "30")

    status = read_daemon_status()

    assert status["state"] == "stale"
    assert status["heartbeat_age_seconds"] == 90.0


def test_market_status_payload_reports_subscribed_and_missing_symbols():
    payload = market_status_payload("AVAXUSDT,USDEUSDT,USDCUSDT", "AVAXUSDT,USDEUSDT", {"AVAXUSDT": 6.4})

    assert payload["subscribed_symbols"] == ["AVAXUSDT", "USDEUSDT", "USDCUSDT"]
    assert payload["display_symbols"] == ["AVAXUSDT", "USDEUSDT"]
    assert payload["snapshot_symbols"] == ["AVAXUSDT"]
    assert payload["snapshot_count"] == 1
    assert payload["missing_snapshot_symbols"] == ["USDEUSDT", "USDCUSDT"]
    assert payload["fresh"] is True
    assert payload["state"] == "fresh"


def test_market_status_payload_exposes_waiting_state_without_snapshot():
    payload = market_status_payload("AVAXUSDT", "AVAXUSDT", {})

    assert payload["fresh"] is False
    assert payload["state"] == "waiting_for_snapshot"


def test_write_status_retries_windows_replace_permission_error(monkeypatch, tmp_path):
    target = tmp_path / "status.json"
    attempts = {"count": 0}
    original_replace = Path.replace

    def flaky_replace(self, target_path):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise PermissionError("locked")
        return original_replace(self, target_path)

    monkeypatch.setattr(liquidation_daemon, "STATUS_PATH", target)
    monkeypatch.setattr(Path, "replace", flaky_replace)

    assert liquidation_daemon._write_status({"state": "running"}) is True
    assert read_daemon_status(target) == {"state": "running"}
    assert attempts["count"] == 2


def test_prepare_market_runtime_restarts_detached_observer(monkeypatch):
    calls = {"terminate": 0, "launch": 0, "supervisor": 0}
    monkeypatch.setattr(liquidation_daemon, "_terminate_observer", lambda _panel: calls.update(terminate=calls["terminate"] + 1))

    class Panel:
        @staticmethod
        def quick_observer_running():
            return True

        @staticmethod
        def displayed_symbols(_running):
            return []

        @staticmethod
        def velocity_start_symbols():
            return ["AVAXUSDT", "ETHUSDT"]

        @staticmethod
        def build_observer_env():
            return ({"SYMBOLS": "AVAXUSDT,ETHUSDT"}, ["AVAXUSDT", "ETHUSDT"])

        @staticmethod
        def launch_observer_process(*_args, **_kwargs):
            calls["launch"] += 1

        @staticmethod
        def start_observer_supervisor():
            calls["supervisor"] += 1

    result = prepare_market_runtime(Panel)

    assert result["state"] == "completed"
    assert result["reused"] is False
    assert result["restarted_existing"] is True
    assert result["env_symbols"] == "AVAXUSDT,ETHUSDT"
    assert result["display_symbols"] == "AVAXUSDT,ETHUSDT"
    assert "warning" not in result
    assert calls == {"terminate": 1, "launch": 1, "supervisor": 1}


def test_prepare_market_runtime_falls_back_when_build_fails_and_observer_running(monkeypatch):
    calls = {"terminate": 0, "launch": 0}
    monkeypatch.setattr(liquidation_daemon, "_terminate_observer", lambda _panel: calls.update(terminate=calls["terminate"] + 1))

    class Panel:
        @staticmethod
        def quick_observer_running():
            return True

        @staticmethod
        def displayed_symbols(_running):
            return []

        @staticmethod
        def velocity_start_symbols():
            return ["AVAXUSDT", "ETHUSDT"]

        @staticmethod
        def build_observer_env():
            raise RuntimeError("database unavailable")

        @staticmethod
        def launch_observer_process(*_args, **_kwargs):
            calls["launch"] += 1

        @staticmethod
        def start_observer_supervisor():
            raise AssertionError("should not be called")

    result = prepare_market_runtime(Panel)

    assert result["state"] == "error"
    assert result["restarted_existing"] is True
    assert "warning" not in result
    assert calls == {"terminate": 1, "launch": 0}
