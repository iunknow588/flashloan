from core.scoped_state import load_runtime_state, save_runtime_state
from runtime.liquidation_daemon import _write_status, read_daemon_status


def test_runtime_state_round_trip_is_scope_checked(tmp_path):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    path = save_runtime_state(tmp_path / "state.json", {"scope": scope, "running": False}, scope)
    assert load_runtime_state(path, scope)["running"] is False


def test_daemon_status_writer_rejects_mismatched_scope(monkeypatch, tmp_path):
    import runtime.liquidation_daemon as daemon
    monkeypatch.setattr(daemon, "STATUS_PATH", tmp_path / "status.json")
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    assert not _write_status({"scope": {**scope, "chain_id": 1}, "state": "running"}, scope=scope)
    assert not (tmp_path / "status.json").exists()


def test_daemon_status_reader_rejects_wrong_persisted_scope(tmp_path):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    path = tmp_path / "status.json"
    path.write_text('{"scope": {"market_id": "m", "chain_id": 1, "network": "eth", "protocol": "aave"}, "state": "running"}', encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="scope mismatch on chain_id"):
        read_daemon_status(path, expected_scope=scope)
