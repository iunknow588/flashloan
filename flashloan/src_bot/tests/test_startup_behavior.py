from datetime import datetime, timedelta, timezone

from web import control_panel, control_panel_control_routes
from web.control_panel import app


class DummyThread:
    started = False

    def __init__(self, target=None, name=None, daemon=None):
        self.target = target
        self.name = name
        self.daemon = daemon

    def start(self):
        DummyThread.started = True


def reset_start_state():
    control_panel.observer_starting = False
    control_panel.observer_start_error = None
    control_panel.selected_symbols = []
    control_panel.observer_start_progress.update(
        {"state": "stopped", "stage": "未启动", "percent": 0, "started_at": None}
    )


def test_start_api_returns_before_building_observer_environment(monkeypatch):
    reset_start_state()
    DummyThread.started = False
    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "configured_database_url", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "velocity_start_symbols", lambda: ["WETHUSDT"])
    monkeypatch.setattr(
        control_panel,
        "build_observer_env",
        lambda: (_ for _ in ()).throw(AssertionError("must run in background")),
    )
    monkeypatch.setattr(control_panel_control_routes.threading, "Thread", DummyThread)

    response = app.test_client().post("/api/start", json={})
    data = response.get_json()

    assert response.status_code == 202
    assert data["starting"] is True
    assert data["message"] == "启动请求已提交，状态面板会显示加载进度。"
    assert control_panel.observer_starting is True
    assert DummyThread.started is True
    reset_start_state()


def test_stale_starting_state_is_cleared(monkeypatch):
    reset_start_state()
    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "OBSERVER_START_TIMEOUT_SECONDS", 1)
    control_panel.observer_starting = True
    control_panel.observer_start_progress["started_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=5)
    ).isoformat(timespec="seconds")

    assert control_panel.clear_stale_observer_start() is True

    assert control_panel.observer_starting is False
    assert "机会观察启动超过 1 秒" in control_panel.observer_start_error
    assert control_panel.observer_start_progress["state"] == "error"
    reset_start_state()
