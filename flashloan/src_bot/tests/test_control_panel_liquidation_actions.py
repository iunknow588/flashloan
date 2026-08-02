from web.control_panel import app


def test_route_failure_state_normalizes_legacy_submission_payload():
    from web.control_panel_liquidation_routes import route_failure_phase, route_failure_state

    response = {
        "state": "submission_allowed",
        "execution_phase": "ready_to_submit",
        "blocked_reasons": [],
        "preflight": {"static_call_passed": True},
    }

    state = route_failure_state("flashloan", response)

    assert state == "submission_failed"
    assert route_failure_phase(response, state) == "ready_to_submit"


def test_route_failure_state_normalizes_static_call_failure():
    from web.control_panel_liquidation_routes import route_failure_phase, route_failure_state

    response = {
        "preflight": {
            "static_call_status": "error",
            "static_call_passed": False,
            "static_call_error": "execution reverted",
        }
    }

    state = route_failure_state("static_call", response)

    assert state == "static_call_failed"
    assert route_failure_phase(response, state) == "static_call_failed"


def test_route_failure_state_normalizes_failed_receipt():
    from web.control_panel_liquidation_routes import route_failure_phase, route_failure_state

    response = {
        "state": "submission_allowed",
        "receipt": {"status": 0},
        "preflight": {"execution_phase": "waiting_receipt"},
    }

    state = route_failure_state("self_funded", response)

    assert state == "confirmed_failed"
    assert route_failure_phase(response, state) == "waiting_receipt"


def test_route_failure_phase_uses_shared_context_fallback():
    from web.control_panel_liquidation_routes import route_failure_phase

    response = {
        "state": "submission_allowed",
        "context": {"phase": "submitting"},
        "preflight": {"context": {"phase": "waiting_receipt"}},
    }

    assert route_failure_phase(response, "submission_failed") == "submitting"


def test_route_failure_records_receipt_timeout_phase_and_tx(monkeypatch):
    from web import control_panel_liquidation_routes as routes

    captured = {}

    def fake_panel_call(name, **kwargs):
        captured["name"] = name
        captured.update(kwargs)

    monkeypatch.setattr(routes, "panel_call", fake_panel_call)

    response = {
        "state": "submission_allowed",
        "execution_phase": "waiting_receipt",
        "tx_hash": "0xtimeout",
        "request": {"debtToCover": "1000"},
        "preflight": {"execution_phase": "waiting_receipt"},
        "receipt": {},
    }

    routes.record_liquidation_route_failure(
        "0x0000000000000000000000000000000000000001",
        "flashloan",
        response,
        TimeoutError("liquidation receipt timeout"),
    )

    assert captured["name"] == "record_liquidation_execution_attempt_safely"
    assert captured["state"] == "submission_failed"
    assert captured["tx_hash"] == "0xtimeout"
    assert captured["preflight"]["execution_phase"] == "waiting_receipt"
    assert captured["preflight"]["route_failure_state"] == "submission_failed"
    assert captured["error"] == "liquidation receipt timeout"


def test_route_failure_records_tx_hash_from_receipt_when_top_level_missing(monkeypatch):
    from web import control_panel_liquidation_routes as routes

    captured = {}

    def fake_panel_call(name, **kwargs):
        captured["name"] = name
        captured.update(kwargs)

    monkeypatch.setattr(routes, "panel_call", fake_panel_call)

    routes.record_liquidation_route_failure(
        "0x0000000000000000000000000000000000000001",
        "flashloan",
        {
            "state": "submission_allowed",
            "preflight": {"execution_phase": "waiting_receipt"},
            "receipt": {"status": 0, "transaction_hash": "0xreceipt"},
        },
        RuntimeError("receipt failed"),
    )

    assert captured["state"] == "confirmed_failed"
    assert captured["tx_hash"] == "0xreceipt"
    assert captured["preflight"]["receipt_status"] == 0


def test_route_failure_redacts_sensitive_error_before_attempt(monkeypatch):
    from web import control_panel_liquidation_routes as routes

    database_url = "postgresql://user:secret-pass@example.com:5432/db?token=abc123"
    private_key = "0x" + "c" * 64
    captured = {}
    monkeypatch.setenv("DATABASE_URL", database_url)

    def fake_panel_call(name, **kwargs):
        captured["name"] = name
        captured.update(kwargs)

    monkeypatch.setattr(routes, "panel_call", fake_panel_call)

    response = {
        "state": "submission_allowed",
        "preflight": {"execution_phase": "submitting"},
        "request": {"debtToCover": "1000"},
    }

    routes.record_liquidation_route_failure(
        "0x0000000000000000000000000000000000000001",
        "flashloan",
        response,
        RuntimeError(f"submit failed: {database_url} private_key={private_key}"),
    )

    assert database_url not in captured["error"]
    assert private_key not in captured["error"]
    assert "secret-pass" not in captured["preflight"]["route_error"]
    assert "abc123" not in captured["preflight"]["route_error"]
    assert "[REDACTED]" in captured["error"]


def test_liquidation_account_api_redacts_sensitive_errors(monkeypatch):
    from web import control_panel

    database_url = "postgresql://user:secret-pass@example.com:5432/db?token=abc123"
    private_key = "0x" + "d" * 64
    monkeypatch.setenv("DATABASE_URL", database_url)

    def fail_payload(account):
        raise RuntimeError(f"db failed: {database_url} private_key={private_key}")

    monkeypatch.setattr(control_panel, "liquidation_account_payload", fail_payload)

    response = app.test_client().get(
        "/api/liquidation/account?account=0x0000000000000000000000000000000000000001"
    )
    data = response.get_json()

    assert response.status_code == 400
    assert database_url not in data["error"]
    assert private_key not in data["error"]
    assert "secret-pass" not in data["error"]
    assert "abc123" not in data["error"]
    assert "[REDACTED]" in data["error"]


def test_liquidation_attempts_api_uses_default_for_invalid_limit(monkeypatch):
    from web import control_panel

    captured = {}

    def recent_attempts(limit=20):
        captured["limit"] = limit
        return {"configured": True, "attempts": [], "stats": {"total": 0}}

    monkeypatch.setattr(control_panel, "recent_liquidation_execution_attempts", recent_attempts)

    response = app.test_client().get("/api/liquidation/execution-attempts?limit=bad")

    assert response.status_code == 200
    assert captured["limit"] == 20


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
        lambda payload, force=False: {
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
        lambda payload, force=False: {
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

    captured = {}
    monkeypatch.setattr(control_panel, "liquidation_execution_payload_for_account", lambda account, **kwargs: {"account": account})
    monkeypatch.setattr(control_panel, "record_liquidation_execution_attempt_safely", lambda **kwargs: captured.setdefault("attempt", kwargs))
    monkeypatch.setattr(
        control_panel,
        "execute_self_funded_liquidation_transaction",
        lambda payload, force=False: {
            "account_report": {"account": payload["account"], "summary": {}},
            "execution_controls": {"execution_enabled": True},
            "execution_phase": "confirmed_success",
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
    assert captured["attempt"]["preflight"]["execution_phase"] == "confirmed_success"


def test_liquidation_account_flashloan_api_returns_tx_details(monkeypatch):
    from web import control_panel

    captured = {}
    monkeypatch.setattr(control_panel, "liquidation_execution_payload_for_account", lambda account, **kwargs: {"account": account})
    monkeypatch.setattr(control_panel, "record_liquidation_execution_attempt_safely", lambda **kwargs: captured.setdefault("attempt", kwargs))
    monkeypatch.setattr(
        control_panel,
        "execute_flashloan_liquidation_transaction",
        lambda payload, force=False: {
            "account_report": {"account": payload["account"], "summary": {}},
            "execution_controls": {"execution_enabled": True},
            "execution_phase": "confirmed_success",
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
    assert captured["attempt"]["preflight"]["execution_phase"] == "confirmed_success"


def test_liquidation_route_success_records_tx_hash_from_receipt(monkeypatch):
    from web import control_panel_liquidation_routes as routes

    captured = {}

    def fake_panel_call(name, **kwargs):
        captured["name"] = name
        captured.update(kwargs)

    monkeypatch.setattr(routes, "panel_call", fake_panel_call)

    routes.record_liquidation_route_success(
        "0x0000000000000000000000000000000000000001",
        "flashloan",
        {
            "execution_phase": "confirmed_success",
            "receipt": {"transaction_hash": "0xsuccess", "status": 1},
        },
    )

    assert captured["state"] == "confirmed_success"
    assert captured["tx_hash"] == "0xsuccess"
    assert captured["preflight"]["receipt_status"] == 1


def test_liquidation_account_execute_api_passes_force_flag(monkeypatch):
    from web import control_panel

    captured = {}
    monkeypatch.setattr(control_panel, "liquidation_execution_payload_for_account", lambda account, **kwargs: {"account": account, "payload_force": kwargs.get("force")})
    monkeypatch.setattr(control_panel, "record_liquidation_execution_attempt_safely", lambda **kwargs: captured.setdefault("attempt", kwargs))
    monkeypatch.setattr(
        control_panel,
        "execute_self_funded_liquidation_transaction",
        lambda payload, force=False: {
            "account_report": {"account": payload["account"], "summary": {}},
            "execution_controls": {"execution_enabled": False, "manual_force": force},
            "mode": "self_funded",
            "sender": "0xsender",
            "request": {},
            "receipt": {"transaction_hash": "0xabc", "status": 1},
            "tx_hash": "0xabc",
            "payload_force": payload["payload_force"],
        },
    )

    response = app.test_client().post(
        "/api/liquidation/account/execute?account=0x0000000000000000000000000000000000000001&force=1"
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["payload_force"] is True
    assert data["execution_controls"]["manual_force"] is True
    assert captured["attempt"]["mode"] == "self_funded_force"


def test_liquidation_account_flashloan_api_passes_force_flag(monkeypatch):
    from web import control_panel

    captured = {}
    monkeypatch.setattr(control_panel, "liquidation_execution_payload_for_account", lambda account, **kwargs: {"account": account, "payload_force": kwargs.get("force")})
    monkeypatch.setattr(control_panel, "record_liquidation_execution_attempt_safely", lambda **kwargs: captured.setdefault("attempt", kwargs))
    monkeypatch.setattr(
        control_panel,
        "execute_flashloan_liquidation_transaction",
        lambda payload, force=False: {
            "account_report": {"account": payload["account"], "summary": {}},
            "execution_controls": {"execution_enabled": False, "manual_force": force},
            "mode": "flashloan",
            "executor": "0xexecutor",
            "request": {},
            "receipt": {"transaction_hash": "0xdef", "status": 1},
            "tx_hash": "0xdef",
            "payload_force": payload["payload_force"],
        },
    )

    response = app.test_client().post(
        "/api/liquidation/account/flashloan?account=0x0000000000000000000000000000000000000001&force=1"
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["payload_force"] is True
    assert data["execution_controls"]["manual_force"] is True
    assert captured["attempt"]["mode"] == "flashloan_force"


def _failing_payload(account, **kwargs):
    return {
        "account": account,
        "executor": "0x0000000000000000000000000000000000000002",
        "request": {"user": account, "debtToCover": "1000"},
        "preflight": {"static_call_required": True},
        "execution_phase": "ready_to_submit",
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
        lambda payload, force=False: (_ for _ in ()).throw(RuntimeError("self funded liquidation failed")),
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
    assert data["execution_phase"] == "ready_to_submit"
    assert data["request"]["debtToCover"] == "1000"
    assert data["preflight"]["static_call_required"] is True
    assert data["account_report"]["summary"]["status"] == "liquidatable"
    assert data["execution_plan"]["execution_ready"] is True
    assert captured["preflight"]["execution_phase"] == "ready_to_submit"
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
        lambda payload, force=False: (_ for _ in ()).throw(RuntimeError("flashloan failed")),
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
    assert data["execution_phase"] == "ready_to_submit"
    assert data["request"]["debtToCover"] == "1000"
    assert data["preflight"]["static_call_required"] is True
    assert data["account_report"]["summary"]["status"] == "liquidatable"
    assert data["execution_plan"]["execution_ready"] is True
    assert captured["preflight"]["execution_phase"] == "ready_to_submit"
    assert captured["mode"] == "flashloan"
    assert captured["state"] == "submission_failed"
    assert captured["error"] == "flashloan failed"


def test_liquidation_account_static_call_and_save_api_records_attempt(monkeypatch):
    from web import control_panel

    captured = {}
    monkeypatch.setattr(
        control_panel,
        "liquidation_execution_payload_for_account",
        lambda account, **kwargs: _failing_payload(account) | {"executor": "0x0000000000000000000000000000000000000002"},
    )
    monkeypatch.setattr(
        control_panel,
        "simulate_liquidation_static_call",
        lambda payload: {
            **payload,
            "preflight": {
                "static_call_required": True,
                "static_call_status": "passed",
                "static_call_passed": True,
                "static_call_error": None,
            },
            "blocked_reasons": [],
            "checks": {},
        },
    )
    monkeypatch.setattr(
        control_panel,
        "record_liquidation_execution_attempt_safely",
        lambda **kwargs: captured.update(kwargs),
    )

    response = app.test_client().post(
        "/api/liquidation/account/0x0000000000000000000000000000000000000001/static-call-and-save",
        json={},
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["preflight"]["static_call_passed"] is True
    assert captured["mode"] == "static_call"
    assert captured["state"] == "static_call_passed"


def test_liquidation_account_execute_failure_archives_canonical_submission_failed(monkeypatch):
    from web import control_panel

    account = "0x0000000000000000000000000000000000000001"
    captured = {}
    payload = _failing_payload(account) | {
        "state": "submission_allowed",
        "submission_allowed": True,
        "dex_quote": {"quote_block": 123},
    }
    monkeypatch.setattr(control_panel, "liquidation_execution_payload_for_account", lambda account, **kwargs: payload)
    monkeypatch.setattr(
        control_panel,
        "execute_self_funded_liquidation_transaction",
        lambda payload, force=False: (_ for _ in ()).throw(RuntimeError("broadcast rejected")),
    )
    monkeypatch.setattr(
        control_panel,
        "record_liquidation_execution_attempt_safely",
        lambda **kwargs: captured.update(kwargs),
    )

    response = app.test_client().post(f"/api/liquidation/account/execute?account={account}")
    data = response.get_json()

    assert response.status_code == 400
    assert data["state"] == "submission_allowed"
    assert captured["state"] == "submission_failed"
    assert captured["quote"] == {"quote_block": 123}
    assert captured["preflight"]["execution_phase"] == "ready_to_submit"
    assert captured["preflight"]["route_failure_state"] == "submission_failed"
    assert captured["error"] == "broadcast rejected"


def test_liquidation_account_flashloan_receipt_zero_archives_confirmed_failed(monkeypatch):
    from web import control_panel

    account = "0x0000000000000000000000000000000000000001"
    captured = {}
    monkeypatch.setattr(control_panel, "liquidation_execution_payload_for_account", lambda account, **kwargs: {"account": account})
    monkeypatch.setattr(
        control_panel,
        "execute_flashloan_liquidation_transaction",
        lambda payload, force=False: {
            "account_report": {"account": payload["account"], "summary": {}},
            "execution_controls": {"execution_enabled": True},
            "execution_phase": "confirmed_failed",
            "mode": "flashloan",
            "executor": "0xexecutor",
            "request": {"debtToCover": "1000"},
            "preflight": {"static_call_passed": True},
            "receipt": {"transaction_hash": "0xdead", "status": 0},
            "tx_hash": "0xdead",
        },
    )
    monkeypatch.setattr(
        control_panel,
        "record_liquidation_execution_attempt_safely",
        lambda **kwargs: captured.update(kwargs),
    )

    response = app.test_client().post(f"/api/liquidation/account/flashloan?account={account}")
    data = response.get_json()

    assert response.status_code == 200
    assert data["receipt"]["status"] == 0
    assert captured["state"] == "confirmed_failed"
    assert captured["preflight"]["execution_phase"] == "confirmed_failed"
    assert captured["preflight"]["receipt_status"] == 0
    assert captured["tx_hash"] == "0xdead"


def test_liquidation_account_flashloan_actual_missing_key_archives_phase(monkeypatch):
    from web import control_panel
    from web import control_panel_liquidation_execute as execute

    account = "0x0000000000000000000000000000000000000001"
    captured = {}
    payload = _failing_payload(account) | {
        "executor": "0x0000000000000000000000000000000000000002",
        "request": {
            "user": account,
            "collateralAsset": "0x0000000000000000000000000000000000000003",
            "debtAsset": "0x0000000000000000000000000000000000000004",
            "debtToCover": "1000",
            "minCollateralSwapOut": "1",
            "minProfitAmount": "1",
            "deadline": "9999999999",
            "gasLimit": "0",
            "swapPath": [],
        },
        "preflight": {"static_call_passed": True, "static_call_status": "passed"},
    }
    controls = {
        "execution_enabled": True,
        "require_static_call": False,
        "flashloan_executor_configured": True,
        "owner_configured": True,
        "max_debt_to_cover": 0,
        "min_profit_base": 0,
        "max_payload_age_seconds": 30,
        "max_quote_age_seconds": 15,
    }
    monkeypatch.setattr(control_panel, "liquidation_execution_payload_for_account", lambda account, **kwargs: payload)
    monkeypatch.setattr(control_panel, "liquidation_execution_controls", lambda: controls)
    monkeypatch.setattr(execute, "liquidation_execution_controls", lambda: controls)
    monkeypatch.setattr(control_panel, "liquidation_executor_private_key", lambda: "")
    monkeypatch.setattr(execute, "liquidation_executor_private_key", lambda: "")
    monkeypatch.setattr(
        control_panel,
        "record_liquidation_execution_attempt_safely",
        lambda **kwargs: captured.update(kwargs),
    )

    response = app.test_client().post(f"/api/liquidation/account/flashloan?account={account}")
    data = response.get_json()

    assert response.status_code == 400
    assert data["error"] == "missing LIQUIDATION_EXECUTION_PRIVATE_KEY"
    assert data["execution_phase"] == "missing_private_key"
    assert captured["state"] == "submission_failed"
    assert captured["preflight"]["execution_phase"] == "missing_private_key"


def test_liquidation_payload_api_blocks_stale_debt_pool_context(monkeypatch):
    from web import control_panel

    account = "0x0000000000000000000000000000000000000001"
    called = {"payload": False}
    monkeypatch.setattr(
        control_panel,
        "liquidation_execution_controls",
        lambda: {
            "max_payload_age_seconds": 30,
            "max_quote_age_seconds": 15,
        },
    )
    monkeypatch.setattr(
        control_panel,
        "liquidation_execution_payload_for_account",
        lambda *args, **kwargs: called.update(payload=True) or {},
    )

    response = app.test_client().get(
        "/api/liquidation/account/payload"
        f"?account={account}"
        "&from=debt_pool"
        "&checked_at=2026-08-02T00:00:00Z"
        "&block_number=100"
        "&latest_block_number=100"
        "&candidate_hash=abc"
    )
    data = response.get_json()

    assert response.status_code == 400
    assert data["execution_phase"] == "context_expired"
    assert "context_expired" in data["blocked_reasons"]
    assert called["payload"] is False


def test_liquidation_payload_api_allows_manual_account_without_route_context(monkeypatch):
    from web import control_panel

    account = "0x0000000000000000000000000000000000000001"
    monkeypatch.setattr(
        control_panel,
        "liquidation_execution_payload_for_account",
        lambda account, **kwargs: {"account": account, "state": "submission_ready"},
    )

    response = app.test_client().get(f"/api/liquidation/account/payload?account={account}")
    data = response.get_json()

    assert response.status_code == 200
    assert data["account"] == account
