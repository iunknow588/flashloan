from web import control_panel
from web.control_panel import app


def test_liquidation_daily_report_api_rebuilds_and_reads_file(monkeypatch, tmp_path):
    from web import control_panel
    from web.data_routes import liquidation_data

    report_path = tmp_path / "daily.json"
    monkeypatch.setattr(liquidation_data, "liquidation_observation_report_path", lambda: report_path)
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(liquidation_data, "build_liquidation_observation_report", lambda database_url: {"generated_at": "2026-07-31T00:00:00+00:00", "borrow_pool": {"count": 1}, "core_opportunities": {"count": 2}, "execution": {"stats": {"total": 3}}})
    monkeypatch.setattr(liquidation_data, "write_liquidation_observation_report", lambda report, path: path.write_text(__import__("json").dumps(report), encoding="utf-8") or path)

    response = app.test_client().post("/api/liquidation/reports/daily")
    data = response.get_json()

    assert response.status_code == 200
    assert data["report"]["core_opportunities"]["count"] == 2
    assert report_path.exists()


def test_liquidation_daily_report_redacts_build_errors(monkeypatch, tmp_path):
    from web import control_panel
    from web.data_routes import liquidation_data

    database_url = "postgresql://user:secret-pass@example.com:5432/db?token=abc123"
    private_key = "0x" + "1" * 64
    report_path = tmp_path / "daily.json"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: database_url)
    monkeypatch.setattr(liquidation_data, "liquidation_observation_report_path", lambda: report_path)

    def fail_report(_database_url):
        raise RuntimeError(f"report failed: {database_url} private_key={private_key}")

    monkeypatch.setattr(liquidation_data, "build_liquidation_observation_report", fail_report)

    response = app.test_client().post("/api/liquidation/reports/daily")
    data = response.get_json()

    assert response.status_code == 400
    assert database_url not in data["error"]
    assert private_key not in data["error"]
    assert "secret-pass" not in data["error"]
    assert "abc123" not in data["error"]
    assert "[REDACTED]" in data["error"]


def test_observations_api_uses_default_for_invalid_limit(monkeypatch):
    from web import control_panel_data_routes

    captured = {}

    def recent_binance(symbol, limit):
        captured["symbol"] = symbol
        captured["limit"] = limit
        return [{"symbol": symbol, "price": 1.0}]

    monkeypatch.setattr(control_panel_data_routes, "recent_binance_price_history", recent_binance)
    monkeypatch.setattr(control_panel_data_routes, "configured_fee_slippage_percent", lambda: 0.3)

    response = app.test_client().get("/api/observations?symbol=UNKNOWN&limit=bad")
    data = response.get_json()

    assert response.status_code == 200
    assert captured["limit"] == 120
    assert data["limit"] == 120
    assert data["rows"][0]["symbol"] == "UNKNOWN"


def test_liquidation_samples_api_returns_manifest(monkeypatch, tmp_path):
    from web import control_panel

    sample_path = tmp_path / "index.json"
    sample_path.write_text(
        '{"schema_version":1,"generated_at":"2026-07-30T10:00:00+00:00","source_count":2,"samples":[{"label":"healthy","status":"ready","file":"healthy.json"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(control_panel, "LIQUIDATION_SAMPLE_LIBRARY_PATH", sample_path)

    client = app.test_client()
    response = client.get("/api/liquidation/samples")
    data = response.get_json()

    assert response.status_code == 200
    assert data["samples"][0]["label"] == "healthy"


def test_liquidation_account_payload_api_requires_executor(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "liquidation_executor_address", lambda: "")

    client = app.test_client()
    response = client.get("/api/liquidation/account/payload?account=0x0000000000000000000000000000000000000001")

    assert response.status_code == 400
    assert "LIQUIDATION_EXECUTOR_ADDRESS" in response.get_json()["error"]


def test_liquidation_accounts_api_persists_to_database(monkeypatch):
    from web import control_panel

    captured = {}
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "ensure_database_schema", lambda database_url: None)
    monkeypatch.setattr(control_panel, "db_prune_liquidation_accounts", lambda database_url, retained_days=365: 0)
    monkeypatch.setattr(
        control_panel,
        "db_upsert_liquidation_accounts",
        lambda database_url, accounts, source="manual", active=True: captured.update({"database_url": database_url, "accounts": accounts, "source": source, "active": active}),
    )

    client = app.test_client()
    response = client.post(
        "/api/liquidation/accounts",
        json={"accounts": ["0x0000000000000000000000000000000000000001", "bad", "0x0000000000000000000000000000000000000001"]},
    )

    assert response.status_code == 200
    assert response.get_json()["source"] == "database"
    assert captured["accounts"] == ["0x0000000000000000000000000000000000000001"]


def test_clear_database_api_rejects_when_background_scan_is_running(monkeypatch):
    from web import control_panel

    monkeypatch.setitem(control_panel.LIQUIDATION_DISCOVERY_CACHE, "running", True)
    monkeypatch.setitem(control_panel.LIQUIDATION_SCAN_CACHE, "running", False)
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "configured_database_url", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "set_control_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(control_panel, "is_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "observer_starting", False)

    assert control_panel.LIQUIDATION_DISCOVERY_LOCK.acquire(blocking=False)
    try:
        response = app.test_client().post("/api/clear", json={})
        data = response.get_json()
    finally:
        control_panel.LIQUIDATION_DISCOVERY_LOCK.release()

    assert response.status_code == 400
    assert data["blockers"]
    assert "后台任务结束" in data["error"]


def test_status_reports_liquidation_background_scan(monkeypatch):
    from web import control_panel

    monkeypatch.setitem(control_panel.LIQUIDATION_DISCOVERY_CACHE, "running", False)
    monkeypatch.setitem(control_panel.LIQUIDATION_SCAN_CACHE, "running", True)
    monkeypatch.setitem(control_panel.LIQUIDATION_SCAN_CACHE, "stage", "debt_pool")
    monkeypatch.setitem(control_panel.LIQUIDATION_SCAN_CACHE, "started_at", "2020-01-01T00:00:00+00:00")
    monkeypatch.setitem(control_panel.LIQUIDATION_SCAN_CACHE, "progress", {"account_count": 10, "scanned_count": 3})
    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "quick_observer_pid", lambda: None)
    monkeypatch.setattr(control_panel, "observer_starting", False)
    monkeypatch.setattr(control_panel, "safe_latest", lambda source: None)
    monkeypatch.setattr(control_panel, "strategy_config", lambda: {})
    monkeypatch.setattr(control_panel, "displayed_symbols", lambda running: [])
    monkeypatch.setattr(control_panel, "restrict_extremes_to_symbols", lambda data, symbols: data)
    monkeypatch.setattr(control_panel, "opportunity_health_rows", lambda data, config: [])
    monkeypatch.setattr(control_panel, "opportunity_health_summary", lambda rows, config: {})
    monkeypatch.setattr(control_panel, "unified_sampling_profile", lambda config: {})

    assert control_panel.LIQUIDATION_SCAN_LOCK.acquire(blocking=False)
    try:
        response = app.test_client().get("/api/status")
        data = response.get_json()
    finally:
        control_panel.LIQUIDATION_SCAN_LOCK.release()

    assert response.status_code == 200
    assert data["running"] is False
    assert data["background_activity"]["liquidation_health_scan"]["running"] is True
    assert data["background_activity"]["liquidation_health_scan"]["stage_label"] == "扫描债务池"
    assert data["background_activity"]["liquidation_health_scan"]["percent"] == 30
    assert data["system_monitor"]["state"] == "initializing"
    assert "后台清算扫描中" in data["system_monitor"]["action"]
    assert "账户 3/10" in data["system_monitor"]["action"]
    assert data["system_monitor"]["background_stage"] == "扫描债务池"
    assert data["system_monitor"]["background_detail"] == "账户 3/10"
    assert data["system_monitor"]["percent"] == 30


def test_clear_database_api_truncates_without_schema_init(monkeypatch):
    from web import control_panel

    monkeypatch.setitem(control_panel.LIQUIDATION_DISCOVERY_CACHE, "running", False)
    monkeypatch.setitem(control_panel.LIQUIDATION_SCAN_CACHE, "running", False)
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "configured_database_url", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "set_control_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(control_panel, "is_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "observer_starting", False)

    executed = []

    class FakeCursor:
        def execute(self, sql):
            executed.append(sql)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            class CursorContext:
                def __enter__(self_inner):
                    return FakeCursor()

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return CursorContext()

    class FakePsycopg:
        def connect(self, database_url, connect_timeout=8):
            assert database_url == "postgresql://example"
            return FakeConnection()

    monkeypatch.setattr(control_panel, "require_psycopg", lambda: FakePsycopg())

    response = app.test_client().post("/api/clear", json={})
    data = response.get_json()

    assert response.status_code == 200
    assert data["cleared"] is True
    assert any("TRUNCATE TABLE observations" in sql for sql in executed)
    assert all("CREATE TABLE" not in sql for sql in executed)


def test_clear_database_api_resets_stale_scan_running_flag(monkeypatch):
    from web import control_panel

    monkeypatch.setitem(control_panel.LIQUIDATION_DISCOVERY_CACHE, "running", True)
    monkeypatch.setitem(control_panel.LIQUIDATION_DISCOVERY_CACHE, "stage", "borrowers")
    monkeypatch.setitem(control_panel.LIQUIDATION_SCAN_CACHE, "running", False)
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "configured_database_url", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "set_control_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(control_panel, "is_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "observer_starting", False)

    executed = []

    class FakeCursor:
        def execute(self, sql):
            executed.append(sql)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            class CursorContext:
                def __enter__(self_inner):
                    return FakeCursor()

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return CursorContext()

    class FakePsycopg:
        def connect(self, database_url, connect_timeout=8):
            assert database_url == "postgresql://example"
            return FakeConnection()

    monkeypatch.setattr(control_panel, "require_psycopg", lambda: FakePsycopg())

    response = app.test_client().post("/api/clear", json={})

    assert response.status_code == 200
    assert control_panel.LIQUIDATION_DISCOVERY_CACHE["running"] is False
    assert control_panel.LIQUIDATION_DISCOVERY_CACHE["stage"] == "idle"
    assert any("TRUNCATE TABLE observations" in sql for sql in executed)


def test_liquidation_discovery_coverage_api_reports_gap(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "liquidation_discovery_progress",
        lambda pool_address: {
            "latest_recent_to_block": 100,
            "earliest_backfill_from_block": 110,
            "success_count": 2,
            "error_count": 0,
            "scanned_block_count": 20,
        },
    )

    response = app.test_client().get(
        "/api/liquidation/discovery-coverage?pool=0x0000000000000000000000000000000000000002"
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["has_gap"] is True
    assert data["latest_gap_from_block"] == 101
    assert data["latest_gap_to_block"] == 109


def test_liquidation_execution_attempts_api_returns_recent_attempts(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "recent_liquidation_execution_attempts",
        lambda limit=20: {
            "configured": True,
            "attempts": [{"id": 7, "state": "submission_blocked"}],
            "stats": {"total": 1, "blocked": 1},
        },
    )

    response = app.test_client().get("/api/liquidation/execution-attempts?limit=5")
    data = response.get_json()

    assert response.status_code == 200
    assert data["attempts"][0]["id"] == 7
    assert data["stats"]["blocked"] == 1


def test_liquidation_failure_samples_api_returns_recent_samples(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "recent_liquidation_failure_samples",
        lambda limit=20: {
            "configured": True,
            "samples": [{"id": 3, "failure_type": "quote_expired"}],
        },
    )

    response = app.test_client().get("/api/liquidation/failure-samples?limit=5")
    data = response.get_json()

    assert response.status_code == 200
    assert data["samples"][0]["failure_type"] == "quote_expired"


def test_liquidation_pause_guard_apis_return_and_clear_state(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "liquidation_pause_guard_status",
        lambda: {"configured": True, "paused": True, "pause_reason": "static_call_failed"},
    )
    monkeypatch.setattr(
        control_panel,
        "clear_liquidation_pause_guard_status",
        lambda: {"configured": True, "paused": False, "pause_reason": None},
    )

    client = app.test_client()
    status = client.get("/api/liquidation/pause-guard").get_json()
    cleared = client.post("/api/liquidation/pause-guard/clear").get_json()

    assert status["paused"] is True
    assert status["pause_reason"] == "static_call_failed"
    assert cleared["paused"] is False
