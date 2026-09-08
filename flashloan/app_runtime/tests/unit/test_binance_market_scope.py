import pytest

from market.binance_market.service import read_binance_market_snapshot, write_binance_market_snapshot


def test_binance_snapshot_scope_is_checked_when_requested(tmp_path):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    path = tmp_path / "snapshot.json"
    write_binance_market_snapshot({"scope": scope, "price": 1}, path, expected_scope=scope)
    assert read_binance_market_snapshot(path, expected_scope=scope)["price"] == 1
    assert read_binance_market_snapshot(path, expected_scope={**scope, "chain_id": 1}) is None


def test_binance_snapshot_scope_rejects_write_mismatch(tmp_path):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    with pytest.raises(ValueError, match="scope mismatch on chain_id"):
        write_binance_market_snapshot(
            {"scope": {**scope, "chain_id": 1}, "price": 1},
            tmp_path / "snapshot.json",
            expected_scope=scope,
        )
