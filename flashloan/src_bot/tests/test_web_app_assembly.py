from web.control_panel import app


def test_control_panel_routes_are_assembled_by_app_factory():
    paths = {rule.rule for rule in app.url_map.iter_rules()}

    assert "/healthz" in paths
    assert "/api/status" in paths
    assert "/api/system-info" in paths
    assert "/account-scan" in paths
    assert "/market-observation" in paths
    assert "/binance-market" in paths
    assert "/dex-arbitrage" in paths
    assert "/execution" in paths
    assert "/audit" in paths
    assert "/config" in paths
    assert "/api/debt-pool/state" in paths
    assert "/api/account-pool/state" in paths
    assert "/api/account-scan/state" in paths
    assert "/api/market-observation/state" in paths
    assert "/api/binance-market/state" in paths
    assert "/api/binance-market/states" in paths
    assert "/api/binance-market/cow-config" in paths
    assert "/api/binance-market/cow-tokens" in paths
    assert "/api/binance-market/cow-tokens/refresh" in paths
    assert "/api/binance-market/cow-support" in paths
    assert "/api/binance-market/cow-quotes" in paths
    assert "/api/binance-market/cow-execution-attempts" in paths
    assert "/api/binance-market/cow-candidate-queue" in paths
    assert "/api/execution/state" in paths
    assert "/api/liquidation-health" in paths
    assert "/api/debt-pool/decision" in paths
    assert "/api/liquidation/account/<account>/static-call-and-save" in paths


def test_system_info_endpoint_owns_scan_version(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "liquidation_scan_version", lambda: "test-version")
    monkeypatch.setattr(
        control_panel,
        "liquidation_scan_refresh_profile",
        lambda: {
            "core_opportunity_refresh_seconds": 1.0,
            "high_frequency_refresh_seconds": 300.0,
            "borrow_health_refresh_seconds": 1800.0,
        },
    )

    data = app.test_client().get("/api/system-info").get_json()

    assert data["version"] == "test-version"
    assert data["scan_policy"]["core_opportunity_refresh_seconds"] == 1.0
    assert data["scan_policy"]["high_frequency_refresh_seconds"] == 300.0
    assert data["scan_policy"]["borrow_health_refresh_seconds"] == 1800.0


def test_status_endpoint_includes_system_info(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "quick_observer_pid", lambda: None)
    monkeypatch.setattr(control_panel, "observer_starting", False)
    monkeypatch.setattr(control_panel, "observer_start_error", None)
    monkeypatch.setattr(control_panel, "latest_binance_extremes_file", lambda: {})
    monkeypatch.setattr(control_panel, "safe_latest", lambda loader: loader() if callable(loader) else {})
    monkeypatch.setattr(control_panel, "control_status_payload", lambda: {})
    monkeypatch.setattr(control_panel, "strategy_config", lambda: {})
    monkeypatch.setattr(control_panel, "displayed_symbols", lambda running: [])
    monkeypatch.setattr(control_panel, "restrict_extremes_to_symbols", lambda rows, symbols: rows)
    monkeypatch.setattr(control_panel, "opportunity_health_rows", lambda rows, config: [])
    monkeypatch.setattr(control_panel, "background_activity_payload", lambda running, starting: {})
    monkeypatch.setattr(control_panel, "observer_progress_payload", lambda *args: {})
    monkeypatch.setattr(control_panel, "system_monitor_payload", lambda *args: {})
    monkeypatch.setattr(control_panel, "opportunity_health_summary", lambda rows, config: {})
    monkeypatch.setattr(control_panel, "unified_sampling_profile", lambda config: {})
    monkeypatch.setattr(control_panel, "liquidation_scan_version", lambda: "status-version")
    monkeypatch.setattr(
        control_panel,
        "liquidation_scan_refresh_profile",
        lambda: {
            "core_opportunity_refresh_seconds": 1.0,
            "high_frequency_refresh_seconds": 300.0,
            "borrow_health_refresh_seconds": 1800.0,
        },
    )

    data = app.test_client().get("/api/status").get_json()

    assert data["system_info"]["version"] == "status-version"
    assert data["system_info"]["scan_policy"]["strategy"] == "core_every_base_cycle_high_frequency_after_5m_borrow_health_after_30m"
    assert "scan_version" not in data


def test_borrow_pool_endpoint_does_not_include_system_version(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: None)

    data = app.test_client().get("/api/liquidation/borrow-pool").get_json()

    encoded = str(data)
    assert "scan_version" not in encoded
    assert "scan_runtime" not in encoded
    assert "scan_cycle_strategy" not in encoded
    assert "system_info" not in encoded
    assert "version" not in data
    assert "version" not in data.get("summary", {})
    assert "version" not in data.get("tiers", {})


def test_liquidation_daemon_status_falls_back_to_ui_runtime(monkeypatch):
    from web import control_panel

    class Config:
        mode = "auto"
        auto_execute = True
        manual_test_completed = True

    class Engine:
        config = Config()

    monkeypatch.setattr(control_panel, "read_daemon_status", lambda: {"state": "stale", "stale": True})
    monkeypatch.setattr(control_panel, "liquidation_engine_instance", Engine())
    monkeypatch.setattr(control_panel, "observer_supervisor_payload", lambda: {"healthy": True, "env_symbols": "AVAXUSDT", "display_symbols": "AVAXUSDT"})
    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: True)
    monkeypatch.setattr(control_panel, "quick_observer_pid", lambda: 123)
    monkeypatch.setattr(control_panel, "displayed_symbols", lambda _running: ["AVAXUSDT"])
    monkeypatch.setattr(control_panel, "liquidation_market_price_snapshot", lambda: {"AVAXUSDT": 6.4})

    data = app.test_client().get("/api/liquidation/daemon/status").get_json()

    assert data["source"] == "ui_runtime"
    assert data["state"] == "running"
    assert data["engine"]["auto_execute"] is True
    assert data["running"] is True


def test_borrow_pool_route_passes_independent_pagination(monkeypatch):
    from web import control_panel

    captured = {}

    def fake_payload(**kwargs):
        captured.update(kwargs)
        return {
            "rows": [],
            "tiers": {"high_frequency_rows": [], "core_opportunity_rows": []},
            "summary": {},
            "pagination": {},
        }

    monkeypatch.setattr(control_panel, "liquidation_borrow_pool_payload", fake_payload)

    response = app.test_client().get(
        "/api/liquidation/borrow-pool?page_size=20&risk_page=2&high_page=3&core_page=4"
    )

    assert response.status_code == 200
    assert captured == {
        "page_size": 20,
        "risk_page": 2,
        "high_page": 3,
        "core_page": 4,
        "skip_schema": True,
    }


def test_borrow_pool_scan_route_passes_force_and_independent_pagination(monkeypatch):
    from web import control_panel

    captured = {}

    def fake_scan(**kwargs):
        captured.update(kwargs)
        return {"rows": [], "summary": {"scanned": True}, "tiers": {}, "pagination": {}}

    monkeypatch.setattr(control_panel, "liquidation_borrow_pool_scan_payload", fake_scan)

    response = app.test_client().post(
        "/api/liquidation/borrow-pool/scan?page_size=20&risk_page=2&high_page=3&core_page=4&force=1",
        json={},
    )

    assert response.status_code == 200
    assert captured == {
        "force": True,
        "page_size": 20,
        "risk_page": 2,
        "high_page": 3,
        "core_page": 4,
    }


def test_page_state_apis_return_consistent_shape(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "quick_observer_pid", lambda: None)
    monkeypatch.setattr(control_panel, "observer_starting", False)
    monkeypatch.setattr(control_panel, "observer_start_error", None)
    monkeypatch.setattr(control_panel, "latest_binance_extremes_file", lambda: {})
    control_panel.LIQUIDATION_SCAN_CACHE.update({"running": False, "stage": "idle", "last_result": {}})
    control_panel.LIQUIDATION_DISCOVERY_CACHE.update({"running": False, "stage": "idle", "last_result": {}})
    control_panel.LIQUIDATION_ACCOUNT_BACKFILL_CACHE.update({"running": False, "stage": "idle", "error": None})
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "load_liquidation_account_registry", lambda force=False: (["0x1"], "database"))
    monkeypatch.setattr(
        control_panel,
        "liquidation_account_registry_window",
        lambda: {"total_count": 1, "active_count": 1, "latest_scan_end_at": "2026-08-02T00:00:00+00:00"},
    )

    client = app.test_client()
    for path, page in [
        ("/api/debt-pool/state", "debt_pool"),
        ("/api/account-scan/state", "account_scan"),
        ("/api/market-observation/state", "market_observation"),
        ("/api/execution/state", "execution"),
    ]:
        response = client.get(path)
        data = response.get_json()

        assert response.status_code == 200
        assert data["page"] == page
        assert "status" in data
        assert "result" in data
        assert "message" in data
        assert "updated_at" in data
        assert "source_event_id" in data
        assert "last_error" in data
        assert "context" in data


def test_page_aliases_point_to_expected_work_surfaces():
    client = app.test_client()

    account_scan = client.get("/account-scan").get_data(as_text=True)
    execution = client.get("/execution").get_data(as_text=True)
    audit = client.get("/audit").get_data(as_text=True)

    assert "accountPoolScanBtn" in account_scan
    assert "auto_return=debt_pool" in account_scan
    assert "executeBtn" in execution
    assert "flashloanBtn" in execution
    assert "attemptBody" in audit


def test_liquidation_page_is_served_as_utf8_html():
    response = app.test_client().get("/liquidation")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "text/html" in response.content_type
    assert "charset=utf-8" in response.content_type.lower()
    assert "核心机会库" in body
    assert "清算价值" in body
    assert "执行状态" in body
    assert "system-info-strip" in body
    assert "scanStrategyText" in body
    assert 'force: "1"' in body
    assert "核心+高频" in body
    assert '<div class="card"><small>系统版本</small>' not in body


def test_liquidation_page_only_forces_manual_borrow_pool_scan():
    body = app.test_client().get("/liquidation").get_data(as_text=True)
    refresh_start = body.index("async function refreshBorrowPool")
    scan_start = body.index("async function scanBorrowPool()")
    settings_start = body.index("async function refreshSettings()")

    refresh_block = body[refresh_start:scan_start]
    scan_block = body[scan_start:settings_start]

    assert 'force: "1"' not in refresh_block
    assert 'force: "1"' in scan_block


def test_liquidation_page_manual_refresh_scans_borrow_pool_health():
    body = app.test_client().get("/liquidation").get_data(as_text=True)

    assert "刷新风险池健康度" in body
    assert "风险池健康度已重新链上扫描" in body
    assert '$("borrowPoolRefreshBtn").onclick = scanBorrowPool;' in body


def test_liquidation_page_explains_database_and_profit_review_states():
    body = app.test_client().get("/liquidation").get_data(as_text=True)

    assert "数据库状态" in body
    assert "databaseState" in body
    assert "databaseMeta" in body
    assert "数据库未配置" in body
    assert "数据库不可用" in body
    assert "数据库端点已禁用" in body
    assert "数据库连接被终止" in body
    assert "已连接" in body
    assert "等待数据库" in body
    assert "自动候选" in body
    assert "阻断/观察" in body
    assert "低于 1U，仅手工测试" in body
    assert "低收益测试项" in body


def test_account_pool_state_api_and_debt_pool_gate(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "latest_binance_extremes_file", lambda: {})
    monkeypatch.setattr(control_panel, "load_liquidation_account_registry", lambda force=False: ([], "database"))
    monkeypatch.setattr(
        control_panel,
        "liquidation_account_registry_window",
        lambda: {"total_count": 0, "active_count": 0, "latest_scan_end_at": None},
    )
    control_panel.LIQUIDATION_SCAN_CACHE.update({"running": False, "stage": "idle", "last_result": {}})

    client = app.test_client()
    account_pool = client.get("/api/account-pool/state").get_json()
    debt_pool = client.get("/api/debt-pool/state").get_json()

    assert account_pool["result"] == "ACCOUNT_POOL_EMPTY"
    assert account_pool["ready"] is False
    assert debt_pool["status"] == "NEED_ACCOUNT_POOL"
    assert debt_pool["result"] == "ACCOUNT_POOL_EMPTY"


def test_account_pool_gate_covers_missing_empty_incomplete_and_ready(monkeypatch):
    from web import control_panel

    cases = [
        {
            "database_url": None,
            "accounts": [],
            "source": "none",
            "window": {"total_count": 0, "active_count": 0, "latest_scan_end_at": None},
            "account_result": "ACCOUNT_POOL_MISSING",
            "debt_status": "NEED_ACCOUNT_POOL",
        },
        {
            "database_url": "postgresql://example",
            "accounts": [],
            "source": "database",
            "window": {"total_count": 0, "active_count": 0, "latest_scan_end_at": None},
            "account_result": "ACCOUNT_POOL_EMPTY",
            "debt_status": "NEED_ACCOUNT_POOL",
        },
        {
            "database_url": "postgresql://example",
            "accounts": ["0x1"],
            "source": "database",
            "window": {"total_count": 1, "active_count": 1, "latest_scan_end_at": None},
            "account_result": "ACCOUNT_POOL_INCOMPLETE",
            "debt_status": "NEED_ACCOUNT_POOL",
        },
        {
            "database_url": "postgresql://example",
            "accounts": ["0x1"],
            "source": "database",
            "window": {"total_count": 1, "active_count": 1, "latest_scan_end_at": "2026-08-02T00:00:00+00:00"},
            "account_result": "ACCOUNT_POOL_READY",
            "debt_status": "IDLE_FRESH",
        },
    ]
    client = app.test_client()
    monkeypatch.setattr(control_panel, "latest_binance_extremes_file", lambda: {})
    control_panel.LIQUIDATION_SCAN_CACHE.update({"running": False, "stage": "idle", "last_result": {}, "error": None})

    for case in cases:
        monkeypatch.setattr(control_panel, "database_url_or_none", lambda value=case["database_url"]: value)
        monkeypatch.setattr(control_panel, "load_liquidation_account_registry", lambda force=False, value=case: (value["accounts"], value["source"]))
        monkeypatch.setattr(control_panel, "liquidation_account_registry_window", lambda value=case: value["window"])

        account_pool = client.get("/api/account-pool/state").get_json()
        debt_pool = client.get("/api/debt-pool/state").get_json()

        assert account_pool["result"] == case["account_result"]
        assert account_pool["ready"] is (case["account_result"] == "ACCOUNT_POOL_READY")
        assert debt_pool["status"] == case["debt_status"]
        if case["debt_status"] == "NEED_ACCOUNT_POOL":
            assert debt_pool["context"]["account_pool"]["reason"]


def test_account_scan_page_preserves_debt_pool_return_intent():
    client = app.test_client()

    body = client.get("/account-scan?from=debt_pool&reason=ACCOUNT_POOL_EMPTY&auto_return=debt_pool").get_data(as_text=True)

    assert "autoReturnToDebtPoolIfReady" in body
    assert 'params.get("auto_return") !== "debt_pool"' in body
    assert 'location.href = "/liquidation?from=account_scan&account_pool_ready=1"' in body
    assert "/api/account-pool/state" in body


def test_debt_pool_decision_api_returns_layered_decision(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "liquidation_borrow_pool_payload",
        lambda: {
            "debt_pool_decision": {
                "status": "CORE_LIQUIDATION_DECISION",
                "result": "CORE_POOL_LIQUIDATABLE",
                "route_intent": "execution",
            }
        },
    )

    response = app.test_client().get("/api/debt-pool/decision")
    data = response.get_json()

    assert response.status_code == 200
    assert data["result"] == "CORE_POOL_LIQUIDATABLE"
    assert data["route_intent"] == "execution"

