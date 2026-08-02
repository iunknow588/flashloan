from web import control_panel_liquidation_audit as audit


def test_decorate_execution_attempts_prefers_row_level_phase():
    rows = [
        {"id": 1, "execution_phase": "confirmed_success", "preflight": {"execution_phase": "ready_to_submit", "context": {"phase": "waiting_receipt"}}},
        {"id": 2, "preflight": {"execution_phase": "ready_to_submit", "context": {"phase": "waiting_receipt"}}},
        {"id": 3, "preflight": {"context": {"phase": "waiting_receipt"}}},
        {"id": 4, "preflight": None},
    ]

    decorated = audit._decorate_execution_attempts(rows)

    assert decorated[0]["execution_phase"] == "confirmed_success"
    assert decorated[1]["execution_phase"] == "ready_to_submit"
    assert decorated[2]["execution_phase"] == "waiting_receipt"
    assert decorated[3]["execution_phase"] is None
    assert "execution_phase" not in rows[1]


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
