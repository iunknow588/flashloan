from web.control_panel_cow_pause import (
    disable_cow_submission_for_startup,
    load_cow_submission_pause_guard,
    set_cow_submission_pause_guard,
)


def test_startup_always_turns_cow_transaction_switch_off(tmp_path):
    path = tmp_path / "cow_submission_pause_guard.json"
    set_cow_submission_pause_guard(paused=False, path=path)

    state = disable_cow_submission_for_startup(path)

    assert state["paused"] is True
    assert state["pause_reason"] == "startup_transaction_switch_off"
    assert load_cow_submission_pause_guard(path)["paused"] is True


def test_missing_cow_transaction_switch_state_defaults_to_off(tmp_path):
    path = tmp_path / "missing_cow_submission_pause_guard.json"

    state = load_cow_submission_pause_guard(path)

    assert state["paused"] is True
    assert state["pause_reason"] == "startup_transaction_switch_off"
