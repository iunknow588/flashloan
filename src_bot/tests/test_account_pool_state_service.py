from web.account_pool_state_service import account_pool_state_payload, evaluate_account_pool_state


def test_account_pool_state_missing_without_database_or_fallback():
    state = evaluate_account_pool_state(
        registry_window={},
        account_count=0,
        account_source="none",
        database_configured=False,
    )

    assert state["result"] == "ACCOUNT_POOL_MISSING"
    assert state["ready"] is False


def test_account_pool_state_empty_when_no_active_accounts():
    state = evaluate_account_pool_state(
        registry_window={"total_count": 0, "active_count": 0},
        account_count=0,
        account_source="database",
    )

    assert state["result"] == "ACCOUNT_POOL_EMPTY"
    assert state["ready"] is False


def test_account_pool_state_incomplete_without_completed_scan_window():
    state = evaluate_account_pool_state(
        registry_window={"total_count": 2, "active_count": 2, "latest_scan_end_at": None},
        account_count=2,
        account_source="database",
    )

    assert state["result"] == "ACCOUNT_POOL_INCOMPLETE"
    assert state["ready"] is False


def test_account_pool_state_ready_with_active_accounts_and_scan_window():
    state = evaluate_account_pool_state(
        registry_window={
            "total_count": 2,
            "active_count": 2,
            "earliest_scan_start_at": "2026-08-01T00:00:00+00:00",
            "latest_scan_end_at": "2026-08-02T00:00:00+00:00",
        },
        account_count=2,
        account_source="database",
    )

    assert state["result"] == "ACCOUNT_POOL_READY"
    assert state["ready"] is True


def test_account_pool_state_payload_redacts_registry_errors(monkeypatch):
    database_url = "postgresql://user:secret-pass@example.com:5432/db?token=abc123"
    private_key = "0x" + "f" * 64
    monkeypatch.setenv("DATABASE_URL", database_url)

    class Panel:
        @staticmethod
        def database_url_or_none():
            return database_url

        @staticmethod
        def load_liquidation_account_registry(force=False):
            raise RuntimeError(f"registry failed: {database_url} private_key={private_key}")

    state = account_pool_state_payload(Panel())
    error = state["registry_window"]["error"]

    assert database_url not in error
    assert private_key not in error
    assert "secret-pass" not in error
    assert "abc123" not in error
    assert "[REDACTED]" in error
