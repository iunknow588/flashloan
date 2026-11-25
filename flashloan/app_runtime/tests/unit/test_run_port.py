import pytest

from app_runtime import run as run_entry


@pytest.fixture(autouse=True)
def _no_live_port_owner(monkeypatch):
    """Keep the port-recycle branch from touching real processes.

    ``resolve_port`` falls back to ``_try_close_port_owner``, which discovers
    the live owner of the port with ``netstat`` and terminates it with
    ``taskkill /F``.  Mocking only ``_port_in_use`` therefore leaves the real
    discovery and the real kill in place, so running this file next to a live
    runtime stops that runtime (exit code 1, no traceback).  Pin both the
    discovery and the kill to in-test doubles; the recycle branch stays
    covered because the fake owner is still looked up and still "killed".
    """
    monkeypatch.setattr(run_entry, "_port_process_ids", lambda port: ["4242"])

    def fake_run(*args, **kwargs):
        return type("Proc", (), {"returncode": 0})()

    monkeypatch.setattr(run_entry.subprocess, "run", fake_run)


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
