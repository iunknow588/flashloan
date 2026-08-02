from web.control_panel import app


def test_control_panel_routes_are_assembled_by_app_factory():
    paths = {rule.rule for rule in app.url_map.iter_rules()}

    assert "/healthz" in paths
    assert "/api/status" in paths
    assert "/account-scan" in paths
    assert "/market-observation" in paths
    assert "/execution" in paths
    assert "/audit" in paths
    assert "/config" in paths
    assert "/api/debt-pool/state" in paths
    assert "/api/account-pool/state" in paths
    assert "/api/account-scan/state" in paths
    assert "/api/market-observation/state" in paths
    assert "/api/execution/state" in paths
    assert "/api/liquidation-health" in paths
    assert "/api/debt-pool/decision" in paths
    assert "/api/liquidation/account/<account>/static-call-and-save" in paths


def test_page_state_apis_return_consistent_shape(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "quick_observer_pid", lambda: None)
    monkeypatch.setattr(control_panel, "observer_starting", False)
    monkeypatch.setattr(control_panel, "observer_start_error", None)
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


def test_account_pool_state_api_and_debt_pool_gate(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
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

