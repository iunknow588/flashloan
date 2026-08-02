from web import control_panel
from web.page_state import ExecutionStatus, PageName
from web.page_state_service import execution_state_payload, store_page_state
from web.page_state_store import PAGE_STATE_STORE
from datetime import datetime, timezone


def test_debt_pool_state_emits_market_alert_once(monkeypatch):
    from web import control_panel

    PAGE_STATE_STORE._states.clear()
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "load_liquidation_account_registry", lambda force=False: (["0x1"], "database"))
    monkeypatch.setattr(
        control_panel,
        "liquidation_account_registry_window",
        lambda: {"total_count": 1, "active_count": 1, "latest_scan_end_at": "2026-08-02T00:00:00+00:00"},
    )
    monkeypatch.setattr(
        control_panel,
        "latest_binance_extremes_file",
        lambda: {
            "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "window_seconds": 1.0,
            "sample_count": 12,
            "active_sample_count": 6,
            "gainer_count": 3,
            "loser_count": 3,
            "market_divergence_index": 2.0,
            "min_change_percent": 0.3,
            "top": [{"symbol": "AVAXUSDT", "change_percent": 3.2}],
            "bottom": [{"symbol": "BTCUSDT", "change_percent": -2.4}],
        },
    )
    control_panel.LIQUIDATION_SCAN_CACHE.update({"running": False, "stage": "idle", "last_result": {}})

    client = control_panel.app.test_client()
    first = client.get("/api/debt-pool/state").get_json()
    second = client.get("/api/debt-pool/state").get_json()

    assert first["status"] == "MARKET_ALERT_RECEIVED"
    assert first["source_event_id"] == first["context"]["market_event"]["event_id"]
    assert first["context"]["route_intent"]["target_page"] == "debt_pool"
    assert second["status"] == "IDLE_FRESH"


def test_execution_state_payload_reflects_recorded_state():
    PAGE_STATE_STORE._states.clear()
    store_page_state(
        PageName.EXECUTION,
        ExecutionStatus.READY_TO_SUBMIT.value,
        message="ready",
        context={"tx_hash": "0xabc"},
    )

    payload = execution_state_payload(control_panel)

    assert payload["status"] == "READY_TO_SUBMIT"
    assert payload["message"] == "ready"
    assert payload["context"]["tx_hash"] == "0xabc"


def test_execution_state_progress_can_be_recorded(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    PAGE_STATE_STORE._states.clear()
    monkeypatch.setattr(execute, "liquidation_executor_address", lambda: "0x0000000000000000000000000000000000000001")
    monkeypatch.setattr(execute, "liquidation_account_payload", lambda account: {"account": account, "summary": {"status": "liquidatable"}})
    monkeypatch.setattr(
        execute,
        "build_liquidation_execution_payload",
        lambda report, **kwargs: {
            "executor": kwargs["executor_address"],
            "request": {"debtToCover": 1, "minProfitAmount": 1},
            "preflight": {},
            "account_report": report,
        },
    )
    monkeypatch.setattr(execute, "liquidation_execution_controls", lambda: {"require_static_call": True, "execution_enabled": True, "slippage_bps": 50, "max_debt_to_cover": 0, "min_profit_base": 0})
    monkeypatch.setattr(execute, "apply_liquidation_submission_state", lambda payload, mode="flashloan": {**payload, "submission_allowed": False, "block_level": "none", "blocked_reasons": [], "force_allowed": False, "state": "submission_blocked"})
    monkeypatch.setattr(execute, "dex_router_address", lambda: "0x0000000000000000000000000000000000000002")

    try:
        execute.liquidation_execution_payload_for_account("0xabc")
    except Exception:
        pass

    payload = execution_state_payload(control_panel)
    assert payload["status"] in {"READY_TO_SUBMIT", "READY_FOR_PREFLIGHT", "SOFT_BLOCKED", "HARD_BLOCKED"}
    assert payload["context"].get("phase") in {"ready_for_preflight", "building_payload", "building_quote", "building_prediction", "loading_account"}


def test_simulate_liquidation_static_call_records_ready_phase(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    PAGE_STATE_STORE._states.clear()
    monkeypatch.setattr(execute, "liquidation_executor_owner_address", lambda: "0x0000000000000000000000000000000000000001")
    monkeypatch.setattr(execute, "scan_context_assets", lambda: ("http://example.invalid", None, None))
    monkeypatch.setattr(
        execute,
        "simulate_request_liquidation_static_call",
        lambda *args, **kwargs: {
            "status": "passed",
            "error": None,
            "parsed": {},
            "simulated_at": "2026-08-02T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        execute,
        "apply_liquidation_submission_state",
        lambda payload, mode="flashloan": {
            **payload,
            "submission_allowed": True,
            "block_level": "none",
            "blocked_reasons": [],
            "state": "submission_ready",
        },
    )

    result = execute.simulate_liquidation_static_call(
        {
            "executor": "0x0000000000000000000000000000000000000002",
            "request": {"user": "0x0000000000000000000000000000000000000003"},
            "preflight": {},
        }
    )

    payload = execution_state_payload(control_panel)
    assert result["execution_phase"] == "ready_to_submit"
    assert payload["status"] == "READY_TO_SUBMIT"
    assert payload["context"]["phase"] == "ready_to_submit"


def test_simulate_liquidation_static_call_records_error_phase(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    PAGE_STATE_STORE._states.clear()
    monkeypatch.setattr(execute, "liquidation_executor_owner_address", lambda: "")

    try:
        execute.simulate_liquidation_static_call(
            {
                "executor": "0x0000000000000000000000000000000000000002",
                "request": {"user": "0x0000000000000000000000000000000000000003"},
                "preflight": {},
            }
        )
    except Exception:
        pass

    payload = execution_state_payload(control_panel)
    assert payload["status"] == "ERROR"
    assert payload["last_error"] == "missing LIQUIDATION_EXECUTOR_OWNER_ADDRESS"
    assert payload["context"]["phase"] == "preflighting"


def test_liquidation_execution_payload_missing_executor_records_error(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    PAGE_STATE_STORE._states.clear()
    monkeypatch.setattr(execute, "liquidation_executor_address", lambda: "")

    try:
        execute.liquidation_execution_payload_for_account("0xabc")
    except Exception:
        pass

    payload = execution_state_payload(control_panel)
    assert payload["status"] == "ERROR"
    assert payload["last_error"] == "missing LIQUIDATION_EXECUTOR_ADDRESS"
    assert payload["context"]["phase"] == "context_received"
