from web.control_panel_cow_pause import (
    disable_cow_submission_for_startup,
    load_cow_submission_pause_guard,
    set_cow_submission_pause_guard,
)


def test_startup_initializes_missing_cow_transaction_switch_off(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / "cow_submission_pause_guard.json"
    state = disable_cow_submission_for_startup(path)

    assert state["paused"] is True
    assert state["pause_reason"] == "startup_transaction_switch_off"
    assert load_cow_submission_pause_guard(path)["paused"] is True


def test_startup_does_not_overwrite_existing_enabled_choice(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / "cow_submission_pause_guard.json"
    set_cow_submission_pause_guard(paused=False, reason="manual_resume", path=path)

    state = disable_cow_submission_for_startup(path)

    assert state["paused"] is False
    assert state["pause_reason"] is None
    assert load_cow_submission_pause_guard(path)["paused"] is False


def test_missing_cow_transaction_switch_state_defaults_to_off(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / "missing_cow_submission_pause_guard.json"

    state = load_cow_submission_pause_guard(path)

    assert state["paused"] is True
    assert state["pause_reason"] == "startup_transaction_switch_off"


def test_cow_transaction_switch_syncs_to_database_parameter_map(monkeypatch, tmp_path):
    from web import control_panel_cow_pause as pause

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

    state = pause.set_cow_submission_pause_guard(paused=False, reason="manual_resume", path=tmp_path / "cow_submission_pause_guard.json")

    assert state["paused"] is False
    assert state["source"] == "database"
    assert stored["paused"] is False
    assert stored["pause_reason"] is None
