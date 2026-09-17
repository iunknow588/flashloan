import pytest

from app_runtime import run as run_entry


def test_resolve_port_auto_advances_when_default_is_occupied(monkeypatch):
    monkeypatch.setattr(
        run_entry,
        "_port_in_use",
        lambda port: port in {5000, 5001, 5002},
    )

    assert run_entry.resolve_port(5000) == (5003, 5000)


def test_resolve_port_keeps_free_default(monkeypatch):
    monkeypatch.setattr(run_entry, "_port_in_use", lambda port: False)

    assert run_entry.resolve_port(5000) == (5000, None)


def test_resolve_port_explicit_port_falls_back_when_recycle_fails(monkeypatch):
    monkeypatch.setattr(run_entry, "_port_in_use", lambda port: port == 5050)

    assert run_entry.resolve_port(5050, explicit=True) == (5051, 5050)
