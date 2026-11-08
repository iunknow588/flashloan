from __future__ import annotations

import time


def test_control_status_payload_is_stable_without_control_activity(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "control_status", {})

    assert control_panel.control_status_payload() == {
        "state": "idle",
        "stage": "",
        "message": "",
        "percent": 0,
    }


def test_control_status_payload_returns_idle_after_ttl(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "control_status",
        {
            "updated_at": time.monotonic() - 10,
            "ttl_seconds": 1,
            "state": "running",
            "stage": "test",
            "message": "expired",
            "percent": 100,
        },
    )

    assert control_panel.control_status_payload()["state"] == "idle"
