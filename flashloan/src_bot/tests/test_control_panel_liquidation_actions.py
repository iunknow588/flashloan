from web.control_panel import app


def test_liquidation_account_payload_api_returns_execution_payload(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "liquidation_executor_address", lambda: "0x0000000000000000000000000000000000000004")
    monkeypatch.setattr(control_panel, "dex_router_address", lambda: "0x0000000000000000000000000000000000000005")
    monkeypatch.setattr(
        control_panel,
        "liquidation_account_payload",
        lambda account: {
            "account": "0x0000000000000000000000000000000000000001",
            "summary": {"status": "liquidatable"},
            "execution_plan": {"execution_ready": True},
            "recommended_candidate": {
                "collateral_asset": "0x0000000000000000000000000000000000000002",
                "debt_asset": "0x0000000000000000000000000000000000000003",
                "amount_to_pass_to_liquidation_call": 1000,
                "min_collateral_swap_out": 900,
                "estimated_profit": {"net_profit_base": 123},
            },
        },
    )

    response = app.test_client().get(
        "/api/liquidation/account/payload?account=0x0000000000000000000000000000000000000001"
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["method"] == "requestLiquidation"
    assert data["request"]["debtToCover"] == "1000"
    assert data["preflight"]["static_call_required"] is True


def test_liquidation_account_preflight_api_returns_static_call_status(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "liquidation_execution_payload_for_account",
        lambda account: {"account": account, "preflight": {"static_call_required": True}},
    )
    monkeypatch.setattr(
        control_panel,
        "simulate_liquidation_static_call",
        lambda payload: {
            **payload,
            "preflight": {
                **payload["preflight"],
                "static_call_status": "passed",
                "static_call_passed": True,
                "static_call_error": None,
                "static_call_simulated_at": "2026-07-30T10:00:00+00:00",
            },
        },
    )

    response = app.test_client().post(
        "/api/liquidation/account/preflight?account=0x0000000000000000000000000000000000000001"
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["preflight"]["static_call_status"] == "passed"
    assert data["preflight"]["static_call_passed"] is True


def test_liquidation_preflight_path_api_returns_static_call_status(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "liquidation_execution_payload_for_account",
        lambda account: {"account": account, "preflight": {"static_call_required": True}},
    )
    monkeypatch.setattr(
        control_panel,
        "simulate_liquidation_static_call",
        lambda payload: {
            **payload,
            "state": "static_call_passed",
            "preflight": {
                **payload["preflight"],
                "static_call_status": "passed",
                "static_call_passed": True,
            },
        },
    )

    response = app.test_client().get(
        "/api/liquidation/preflight/0x0000000000000000000000000000000000000001"
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["account"] == "0x0000000000000000000000000000000000000001"
    assert data["preflight"]["static_call_passed"] is True


def test_liquidation_account_execute_api_returns_self_funded_tx_details(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "liquidation_execution_payload_for_account", lambda account, **kwargs: {"account": account})
    monkeypatch.setattr(control_panel, "record_liquidation_execution_attempt_safely", lambda **kwargs: None)
    monkeypatch.setattr(
        control_panel,
        "execute_self_funded_liquidation_transaction",
        lambda payload: {
            "account_report": {"account": payload["account"], "summary": {}},
            "execution_controls": {"execution_enabled": True},
            "mode": "self_funded",
            "sender": "0xsender",
            "receipt": {"transaction_hash": "0xabc", "status": 1},
            "tx_hash": "0xabc",
        },
    )

    response = app.test_client().post(
        "/api/liquidation/account/execute?account=0x0000000000000000000000000000000000000001"
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["mode"] == "self_funded"
    assert data["tx_hash"] == "0xabc"
    assert data["receipt"]["status"] == 1


def test_liquidation_account_flashloan_api_returns_tx_details(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "liquidation_execution_payload_for_account", lambda account: {"account": account})
    monkeypatch.setattr(control_panel, "record_liquidation_execution_attempt_safely", lambda **kwargs: None)
    monkeypatch.setattr(
        control_panel,
        "execute_flashloan_liquidation_transaction",
        lambda payload: {
            "account_report": {"account": payload["account"], "summary": {}},
            "execution_controls": {"execution_enabled": True},
            "mode": "flashloan",
            "executor": "0xexecutor",
            "receipt": {"transaction_hash": "0xdef", "status": 1},
            "tx_hash": "0xdef",
        },
    )

    response = app.test_client().post(
        "/api/liquidation/account/flashloan?account=0x0000000000000000000000000000000000000001"
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["mode"] == "flashloan"
    assert data["tx_hash"] == "0xdef"
    assert data["receipt"]["status"] == 1


def _failing_payload(account):
    return {
        "account": account,
        "executor": "0x0000000000000000000000000000000000000002",
        "request": {"user": account, "debtToCover": "1000"},
        "preflight": {"static_call_required": True},
        "account_report": {
            "account": account,
            "summary": {"status": "liquidatable", "health_factor": 0.98},
            "execution_plan": {"execution_ready": True, "reason": "ready"},
        },
        "execution_controls": {"execution_enabled": True},
    }


def test_liquidation_account_execute_api_returns_context_on_failure(monkeypatch):
    from web import control_panel

    captured = {}
    monkeypatch.setattr(control_panel, "liquidation_execution_payload_for_account", lambda account, **kwargs: _failing_payload(account))
    monkeypatch.setattr(
        control_panel,
        "execute_self_funded_liquidation_transaction",
        lambda payload: (_ for _ in ()).throw(RuntimeError("self funded liquidation failed")),
    )
    monkeypatch.setattr(
        control_panel,
        "record_liquidation_execution_attempt_safely",
        lambda **kwargs: captured.update(kwargs),
    )

    response = app.test_client().post(
        "/api/liquidation/account/execute?account=0x0000000000000000000000000000000000000001"
    )
    data = response.get_json()

    assert response.status_code == 400
    assert data["error"] == "self funded liquidation failed"
    assert data["request"]["debtToCover"] == "1000"
    assert data["preflight"]["static_call_required"] is True
    assert data["account_report"]["summary"]["status"] == "liquidatable"
    assert data["execution_plan"]["execution_ready"] is True
    assert captured["mode"] == "self_funded"
    assert captured["state"] == "submission_failed"
    assert captured["error"] == "self funded liquidation failed"


def test_liquidation_account_flashloan_api_returns_context_on_failure(monkeypatch):
    from web import control_panel

    captured = {}
    monkeypatch.setattr(control_panel, "liquidation_execution_payload_for_account", _failing_payload)
    monkeypatch.setattr(
        control_panel,
        "execute_flashloan_liquidation_transaction",
        lambda payload: (_ for _ in ()).throw(RuntimeError("flashloan failed")),
    )
    monkeypatch.setattr(
        control_panel,
        "record_liquidation_execution_attempt_safely",
        lambda **kwargs: captured.update(kwargs),
    )

    response = app.test_client().post(
        "/api/liquidation/account/flashloan?account=0x0000000000000000000000000000000000000001"
    )
    data = response.get_json()

    assert response.status_code == 400
    assert data["error"] == "flashloan failed"
    assert data["request"]["debtToCover"] == "1000"
    assert data["preflight"]["static_call_required"] is True
    assert data["account_report"]["summary"]["status"] == "liquidatable"
    assert data["execution_plan"]["execution_ready"] is True
    assert captured["mode"] == "flashloan"
    assert captured["state"] == "submission_failed"
    assert captured["error"] == "flashloan failed"
