from web.control_panel_liquidation_pause import (
    load_pause_guard_state,
    pause_guard_controls,
    record_pause_guard_event,
)


def test_pause_guard_pauses_after_consecutive_failures(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
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


def test_pause_guard_success_resets_failure_count(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
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


def test_pause_guard_syncs_to_database_parameter_map(monkeypatch, tmp_path):
    from web import control_panel_liquidation_pause as pause

    database_url = "postgresql://example"
    stored = {}

    def fake_load(database_url_arg, namespace):
        return dict(stored)

    def fake_save(database_url_arg, namespace, values):
        stored.clear()
        stored.update(values)
        return dict(values)

    def fake_sync(page_key, payload):
        stored.update(payload)

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(pause, "load_control_panel_parameter_map", fake_load)
    monkeypatch.setattr(pause, "save_control_panel_parameter_map", fake_save)
    monkeypatch.setattr(pause, "sync_page_parameter_file", fake_sync)

    state = pause.clear_pause_guard(tmp_path / "liquidation_pause_guard.json")

    assert state["paused"] is False
    assert state["source"] == "database"
    assert stored["paused"] is False
    assert stored["consecutive_failure_count"] == 0
