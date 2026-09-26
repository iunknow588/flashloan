import core.db.storage_liquidation_failures as storage
from core.kv_rows import upsert_row


def test_failure_sample_reader_loads_local_kv_rows(monkeypatch, tmp_path):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    kv_path = str(tmp_path / "failures.sqlite3")
    upsert_row(kv_path, "liquidation_failure_samples", "1", {**scope, "failure_type": "reverted", "account": "0xabc"})

    rows = storage.load_recent_liquidation_failure_samples(kv_path)

    assert rows[0]["failure_type"] == "reverted"
    assert rows[0]["account"] == "0xabc"
