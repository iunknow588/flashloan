import importlib


audit = importlib.import_module("web.control_panel_liquidation_audit")


def test_decorate_execution_attempts_prefers_row_level_phase():
    rows = [
        {"id": 1, "execution_phase": "confirmed_success", "preflight": {"execution_phase": "ready_to_submit", "context": {"phase": "waiting_receipt"}}},
        {"id": 2, "preflight": {"execution_phase": "ready_to_submit", "context": {"phase": "waiting_receipt"}}},
        {"id": 3, "preflight": {"context": {"phase": "waiting_receipt"}}},
        {"id": 4, "preflight": None, "receipt": {"status": 0}},
    ]

    decorated = audit._decorate_execution_attempts(rows)

    assert decorated[0]["execution_phase"] == "confirmed_success"
    assert decorated[1]["execution_phase"] == "ready_to_submit"
    assert decorated[2]["execution_phase"] == "waiting_receipt"
    assert decorated[3]["execution_phase"] == "confirmed_failed"
    assert "execution_phase" not in rows[1]


def test_decorate_execution_attempts_normalizes_tx_hash_from_context_and_receipt():
    rows = [
        {"id": 1, "tx_hash": "0xtop", "preflight": {"tx_hash": "0xpreflight"}},
        {"id": 2, "preflight": {"context": {"tx_hash": "0xnested"}}},
        {"id": 3, "receipt": {"transaction_hash": "0xreceipt"}},
    ]

    decorated = audit._decorate_execution_attempts(rows)

    assert decorated[0]["tx_hash"] == "0xtop"
    assert decorated[1]["tx_hash"] == "0xnested"
    assert decorated[2]["tx_hash"] == "0xreceipt"
    assert "tx_hash" not in rows[1]


def test_normalize_execution_phase_uses_shared_priority():
    row = {
        "state": "submission_failed",
        "context": {"phase": "top_context_phase"},
        "preflight": {"execution_phase": "preflight_phase", "context": {"phase": "nested_phase"}},
    }

    assert audit.normalize_execution_phase(row) == "preflight_phase"


def test_build_failure_sample_payload_includes_canonical_execution_fields():
    payload = audit.build_failure_sample_payload(
        mode="flashloan",
        state="confirmed_failed",
        request_payload={"user": "0x1"},
        quote={"quote_block": 123},
        preflight={"execution_phase": "waiting_receipt"},
        tx_hash="0xdead",
        receipt={"status": 0, "transaction_hash": "0xdead"},
        error="receipt status 0",
    )

    assert payload["execution_phase"] == "waiting_receipt"
    assert payload["tx_hash"] == "0xdead"
    assert payload["receipt_status"] == 0
    assert payload["retryable"] is False
    assert payload["request"] == {"user": "0x1"}
    assert payload["quote"] == {"quote_block": 123}


def test_build_failure_sample_payload_falls_back_to_receipt_tx_hash():
    payload = audit.build_failure_sample_payload(
        mode="flashloan",
        state="confirmed_success",
        receipt={"status": 1, "transaction_hash": "0xfromreceipt"},
    )

    assert payload["execution_phase"] == "confirmed_success"
    assert payload["tx_hash"] == "0xfromreceipt"
    assert payload["receipt_status"] == 1


def test_failure_retryable_allows_only_soft_blocker_retry():
    assert audit.failure_retryable("submission_blocked", ["static_call_required"]) is True
    assert audit.failure_retryable("submission_blocked", ["missing_executor"]) is False
    assert audit.failure_retryable("static_call_failed", []) is True
    assert audit.failure_retryable("confirmed_failed", []) is False


def test_empty_execution_attempt_stats_is_stable():
    assert audit.empty_execution_attempt_stats() == {
        "total": 0,
        "blocked": 0,
        "submitted": 0,
        "confirmed_success": 0,
        "confirmed_failed": 0,
        "static_call_failed": 0,
        "errors": 0,
    }


def test_audit_error_message_is_redacted(monkeypatch):
    database_url = "postgresql://user:secret-pass@example.com:5432/db?token=abc123"
    private_key = "0x" + "a" * 64
    monkeypatch.setenv("DATABASE_URL", database_url)

    result = audit._safe_error_message(f"connection failed: {database_url} private_key={private_key}")

    assert database_url not in result
    assert "secret-pass" not in result
    assert "abc123" not in result
    assert private_key not in result
    assert "[REDACTED]" in result
    assert "private_key=[REDACTED]" in result


def test_failure_sample_payload_keeps_redacted_error(monkeypatch):
    database_url = "postgresql://user:secret-pass@example.com:5432/db?token=abc123"
    private_key = "0x" + "b" * 64
    monkeypatch.setenv("DATABASE_URL", database_url)
    safe_error = audit._safe_error_message(f"submit failed: {database_url} private_key={private_key}")

    payload = audit.build_failure_sample_payload(
        mode="flashloan",
        state="submission_failed",
        request_payload={"debtToCover": "1000"},
        error=safe_error,
    )

    assert database_url not in payload["error"]
    assert private_key not in payload["error"]
    assert "[REDACTED]" in payload["error"]
    assert payload["retryable"] is True


def test_pause_guard_record_uses_default_for_invalid_threshold(monkeypatch):
    captured = {}
    monkeypatch.setenv("LIQUIDATION_AUTO_PAUSE_FAILURE_THRESHOLD", "bad")
    monkeypatch.setattr(audit, "LIQUIDATION_PAUSE_GUARD_PATH", object())

    def record_event(path, **kwargs):
        captured["path"] = path
        captured.update(kwargs)

    monkeypatch.setattr(audit, "record_pause_guard_event", record_event)

    audit._record_pause_guard_if_configured(
        "submission_failed",
        ["static_call_failed"],
        "submit failed",
    )

    assert captured["threshold"] == 3
    assert captured["enabled"] is True
    assert captured["state_name"] == "submission_failed"
