from web.control_panel_liquidation_pause import (
    load_pause_guard_state,
    pause_guard_controls,
    record_pause_guard_event,
)


def test_pause_guard_pauses_after_consecutive_failures(tmp_path):
    path = tmp_path / "pause_guard.json"

    record_pause_guard_event(
        path,
        state_name="submission_failed",
        blocked_reasons=["static_call_failed"],
        error=None,
        enabled=True,
        threshold=2,
    )
    state = record_pause_guard_event(
        path,
        state_name="confirmed_failed",
        blocked_reasons=[],
        error="tx reverted",
        enabled=True,
        threshold=2,
    )

    assert state["paused"] is True
    assert state["consecutive_failure_count"] == 2
    controls = pause_guard_controls(path, enabled=True, threshold=2)
    assert controls["auto_pause_active"] is True
    assert controls["auto_pause_reason"] == "tx reverted"


def test_pause_guard_success_resets_failure_count(tmp_path):
    path = tmp_path / "pause_guard.json"
    record_pause_guard_event(
        path,
        state_name="submission_failed",
        blocked_reasons=["quote_expired"],
        error=None,
        enabled=True,
        threshold=1,
    )

    record_pause_guard_event(
        path,
        state_name="confirmed_success",
        enabled=True,
        threshold=1,
    )

    state = load_pause_guard_state(path)
    assert state["paused"] is False
    assert state["consecutive_failure_count"] == 0
