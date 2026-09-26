import core.db.storage_liquidation_accounts as storage
import time

from core.kv_rows import load_rows, upsert_row


def test_latest_liquidation_reports_validates_database_row_scope(monkeypatch, tmp_path):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    kv_path = tmp_path / "accounts.sqlite3"
    monkeypatch.setenv("FLASHLOAN_KV_PATH", str(kv_path))
    upsert_row(str(kv_path), "liquidation_accounts", "m|56|0xabc", {**scope, "account": "0xabc", "last_report_json": {"summary": {}}})

    reports = storage.load_latest_liquidation_account_reports(str(kv_path))

    assert reports[0]["market_id"] == "m"
    assert reports[0]["chain_id"] == 56


def test_prune_liquidation_accounts_removes_old_history_but_keeps_active(tmp_path):
    kv_path = str(tmp_path / "accounts.sqlite3")
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "aave"}
    old = time.time() - 999 * 86400

    upsert_row(str(kv_path), "liquidation_account_health_scans", "m|56|0xabc|old", {**scope, "account": "0xabc", "scanned_at": old})
    upsert_row(str(kv_path), "liquidation_account_health_scans", "m|56|0xabc|new", {**scope, "account": "0xabc", "scanned_at": time.time()})
    upsert_row(str(kv_path), "liquidation_accounts", "m|56|0xold", {**scope, "account": "0xold", "active": False, "last_scanned_at": old})
    upsert_row(str(kv_path), "liquidation_accounts", "m|56|0xnew", {**scope, "account": "0xnew", "active": True, "last_scanned_at": old})

    assert storage.prune_liquidation_accounts(str(kv_path), retained_days=365) == 2
    assert len(load_rows(str(kv_path), "liquidation_account_health_scans")) == 1
    assert {row["account"] for row in load_rows(str(kv_path), "liquidation_accounts")} == {"0xnew"}
