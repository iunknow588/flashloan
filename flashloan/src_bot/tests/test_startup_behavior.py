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
    control_panel.observer_process = None
    control_panel.selected_symbols = []
    control_panel.observer_start_progress.update(
        {"state": "stopped", "stage": "未启动", "percent": 0, "started_at": None}
    )
    control_panel.observer_supervisor_stop.set()
    control_panel.observer_supervisor_state.update(
        {
            "enabled": False,
            "heartbeat_at": 0.0,
            "restart_count": 0,
            "last_restart_at": 0.0,
            "next_restart_at": 0.0,
            "last_exit_code": None,
            "last_error": None,
        }
    )
    control_panel.control_status.update(
        {"state": "stopped", "stage": "", "message": "", "percent": 0, "updated_at": 0.0, "ttl_seconds": 0.0}
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


def test_start_api_redacts_database_config_error(monkeypatch):
    reset_start_state()
    database_url = "postgresql://user:secret-pass@example.com:5432/db?token=abc123"
    private_key = "0x" + "a" * 64
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: False)

    def fail_database_url():
        raise RuntimeError(f"db failed: {database_url} private_key={private_key}")

    monkeypatch.setattr(control_panel, "configured_database_url", fail_database_url)

    response = app.test_client().post("/api/start", json={})
    data = response.get_json()

    assert response.status_code == 400
    for value in (
        data["error"],
        control_panel.observer_start_error,
        control_panel.observer_start_progress["stage"],
        control_panel.control_status["message"],
    ):
        assert database_url not in value
        assert private_key not in value
        assert "secret-pass" not in value
        assert "abc123" not in value
        assert "[REDACTED]" in value
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


def test_observer_supervisor_restarts_crashed_process(monkeypatch):
    reset_start_state()
    events = []

    class CrashedProcess:
        pid = 11

        @staticmethod
        def poll():
            return 1

    class RestartedProcess:
        pid = 22

        @staticmethod
        def poll():
            return None

    control_panel.observer_process = CrashedProcess()
    control_panel.observer_supervisor_state["enabled"] = True
    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "build_observer_env", lambda: ({"DATABASE_URL": "postgresql://example"}, ["AVAXUSDT"]))
    monkeypatch.setattr(control_panel, "launch_observer_process", lambda env, symbols: events.append((env, symbols)) or RestartedProcess())
    monkeypatch.setattr(control_panel, "set_control_status", lambda *args, **kwargs: None)

    result = control_panel.observer_supervisor_once(now=100.0)

    assert result["action"] == "restarted"
    assert result["pid"] == 22
    assert events == [({"DATABASE_URL": "postgresql://example"}, ["AVAXUSDT"])]
    assert control_panel.observer_supervisor_state["restart_count"] == 1
    assert control_panel.observer_supervisor_state["last_restart_at"] == 100.0
    assert control_panel.observer_supervisor_state["next_restart_at"] <= 130.0
    reset_start_state()


def test_observer_supervisor_honors_backoff(monkeypatch):
    reset_start_state()
    control_panel.observer_supervisor_state.update(
        {
            "enabled": True,
            "restart_count": 1,
            "next_restart_at": 120.0,
        }
    )
    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: False)

    result = control_panel.observer_supervisor_once(now=110.0)

    assert result["action"] == "backoff"
    assert result["next_restart_at"] == 120.0
    reset_start_state()


def test_build_observer_env_prefers_detailed_liquidation_account_assets(monkeypatch):
    from web import control_panel

    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0x0000000000000000000000000000000000000001")
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "configured_database_url", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "aave_rpc_urls", lambda: ["https://rpc.example"])
    monkeypatch.setattr(
        control_panel,
        "load_aave_reserve_assets",
        lambda *args, **kwargs: [
            {"token_symbol": "WAVAX", "token_address": "0xavax", "binance_symbol": "AVAXUSDT"},
            {"token_symbol": "USDC", "token_address": "0xusdc", "binance_symbol": "USDCUSDT"},
            {"token_symbol": "AAVE", "token_address": "0xaave", "binance_symbol": "AAVEUSDT"},
        ],
    )
    monkeypatch.setattr(control_panel, "db_load_liquidation_core_opportunity_pool", lambda *args, **kwargs: [])
    monkeypatch.setattr(control_panel, "db_load_liquidation_high_frequency_pool", lambda *args, **kwargs: [])
    monkeypatch.setattr(control_panel, "db_load_liquidation_borrow_health_pool", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        control_panel,
        "db_load_latest_liquidation_account_reports",
        lambda *args, **kwargs: [
            {
                "report": {
                    "positions": [
                        {"symbol": "AVAXUSDT", "token_symbol": "WAVAX", "debt_value_base": 100},
                        {"symbol": "USDCUSDT", "token_symbol": "USDC", "collateral_value_base": 200},
                    ]
                }
            }
        ],
    )
    monkeypatch.setattr(control_panel, "env_urls", lambda *args, **kwargs: ["https://binance.example"])
    monkeypatch.setattr(control_panel, "resolve_aave_binance_overlap_symbols", lambda *args, **kwargs: ["AAVEUSDT"])

    env, symbols = control_panel.build_observer_env()

    assert env["BINANCE_SYMBOL_SELECTION"] == "explicit"
    assert env["SYMBOLS"] == "AVAXUSDT,USDCUSDT"
    assert env["AAVE_VERIFICATION_ENABLED"] == "false"
    assert symbols == ["AVAXUSDT", "USDCUSDT"]


def test_observer_supervisor_restart_error_is_redacted(monkeypatch):
    reset_start_state()
    database_url = "postgresql://user:secret-pass@example.com:5432/db?token=abc123"
    private_key = "0x" + "b" * 64

    class CrashedProcess:
        @staticmethod
        def poll():
            return 1

    control_panel.observer_process = CrashedProcess()
    control_panel.observer_supervisor_state["enabled"] = True
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: False)

    def fail_build_env():
        raise RuntimeError(f"restart failed: {database_url} private_key={private_key}")

    monkeypatch.setattr(control_panel, "build_observer_env", fail_build_env)

    result = control_panel.observer_supervisor_once(now=100.0)

    assert result["action"] == "restart_failed"
    for value in (
        result["error"],
        control_panel.observer_supervisor_state["last_error"],
        control_panel.observer_start_progress["stage"],
    ):
        assert database_url not in value
        assert private_key not in value
        assert "secret-pass" not in value
        assert "abc123" not in value
        assert "[REDACTED]" in value
    reset_start_state()


def test_start_observer_background_redacts_build_error(monkeypatch):
    reset_start_state()
    database_url = "postgresql://user:secret-pass@example.com:5432/db?token=abc123"
    private_key = "0x" + "c" * 64
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "configured_database_url", lambda: database_url)
    control_panel.observer_starting = True

    def fail_build_env():
        raise RuntimeError(f"background failed: {database_url} private_key={private_key}")

    monkeypatch.setattr(control_panel, "build_observer_env", fail_build_env)

    control_panel.start_observer_background()

    for value in (
        control_panel.observer_start_error,
        control_panel.observer_start_progress["stage"],
    ):
        assert database_url not in value
        assert private_key not in value
        assert "secret-pass" not in value
        assert "abc123" not in value
        assert "[REDACTED]" in value
    assert control_panel.observer_starting is False
    reset_start_state()


def test_initialize_liquidation_runtime_does_not_start_engine_when_execution_disabled(monkeypatch):
    calls = {"engine": 0, "registry": 0}

    monkeypatch.setenv("LIQUIDATION_UI_SCAN_ENABLED", "false")
    monkeypatch.setenv("LIQUIDATION_AUTO_EXECUTE", "false")
    monkeypatch.setenv("LIQUIDATION_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("LIQUIDATION_ENGINE_ENABLED", "false")
    monkeypatch.setattr(control_panel, "start_liquidation_engine_runtime", lambda force=False: calls.update(engine=calls["engine"] + 1))
    monkeypatch.setattr(control_panel, "load_liquidation_account_registry", lambda force=False: calls.update(registry=calls["registry"] + 1))

    control_panel.initialize_liquidation_runtime()

    assert calls == {"engine": 0, "registry": 1}


def test_initialize_liquidation_runtime_autostarts_engine_when_execution_enabled(monkeypatch):
    engine_calls = []
    registry_calls = []

    monkeypatch.setenv("LIQUIDATION_UI_SCAN_ENABLED", "false")
    monkeypatch.setenv("LIQUIDATION_AUTO_EXECUTE", "true")
    monkeypatch.setenv("LIQUIDATION_EXECUTION_ENABLED", "true")
    monkeypatch.delenv("LIQUIDATION_ENGINE_ENABLED", raising=False)
    monkeypatch.setattr(control_panel, "start_liquidation_engine_runtime", lambda force=False: engine_calls.append(force))
    monkeypatch.setattr(control_panel, "load_liquidation_account_registry", lambda force=False: registry_calls.append(force))

    control_panel.initialize_liquidation_runtime()

    assert engine_calls == [True]
    assert registry_calls == [True]
