from web.account_pool_state_service import evaluate_account_pool_state


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

