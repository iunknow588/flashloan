import pytest

from core.json_io import append_scoped_jsonl, read_scoped_jsonl


def test_scoped_jsonl_round_trip(tmp_path):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    path = tmp_path / "events.jsonl"
    append_scoped_jsonl(path, {"scope": scope, "event": "observed"}, scope)
    append_scoped_jsonl(path, {"scope": scope, "event": "quoted"}, scope)
    assert [row["event"] for row in read_scoped_jsonl(path, scope)] == ["observed", "quoted"]


def test_scoped_jsonl_rejects_wrong_scope_with_line_number(tmp_path):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    path = tmp_path / "events.jsonl"
    path.write_text('{"scope": {"market_id": "m", "chain_id": 1, "network": "eth", "protocol": "aave"}}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 1.*scope mismatch on chain_id"):
        read_scoped_jsonl(path, scope)
