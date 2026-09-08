import pytest

from core.json_io import read_scoped_json, write_scoped_json


def test_scoped_json_round_trip_validates_scope(tmp_path):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    path = write_scoped_json(tmp_path / "state.json", {"scope": scope, "value": 1}, scope)
    assert read_scoped_json(path, scope)["value"] == 1


def test_scoped_json_rejects_wrong_scope_before_write_and_read(tmp_path):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    with pytest.raises(ValueError, match="scope mismatch on chain_id"):
        write_scoped_json(tmp_path / "state.json", {"scope": {**scope, "chain_id": 1}}, scope)
    path = tmp_path / "bad.json"
    path.write_text('{"value": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="scope missing"):
        read_scoped_json(path, scope)
