import json

from market.tools.watch_bnb_venus_account_scan_status import build_scan_status, write_scan_status


def test_bnb_venus_account_scan_status_detects_rpc_blocked(tmp_path):
    (tmp_path / "venus_borrower_registry.json").write_text(
        json.dumps(
            {
                "storage_mode": "file_only",
                "discovery_mode": "borrow_events_only",
                "scan_complete": False,
                "account_count": 1,
                "event_count": 1,
                "events_jsonl": "venus_borrower_events_2mo.jsonl",
                "accounts": [{"account": "0x0000000000000000000000000000000000000001"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "venus_borrower_accounts.txt").write_text("0x0000000000000000000000000000000000000001\n", encoding="utf-8")
    (tmp_path / "venus_borrower_events_2mo.jsonl").write_text(
        json.dumps(
            {
                "account": "0x0000000000000000000000000000000000000001",
                "event": "Borrow",
                "vtoken": "0x0000000000000000000000000000000000000011",
                "block_number": 10,
                "transaction_hash": "0x01",
                "log_index": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "venus_two_month_market_scan_progress.json").write_text(
        json.dumps({"status": "running_market", "market_index": 1, "market_count": 52, "selected_market_count": 52}),
        encoding="utf-8",
    )
    (tmp_path / "venus_borrow_scan_progress.json").write_text(
        json.dumps({"current_from_block": 100, "current_to_block": 100, "error": "all BNB RPCs failed"}),
        encoding="utf-8",
    )

    status = build_scan_status(tmp_path, pid=None)
    paths = write_scan_status(tmp_path, status)

    assert status["status"] == "rpc_blocked"
    assert status["registry"]["account_count"] == 1
    assert paths["latest"].endswith("venus_account_scan_status_latest.json")
    assert (tmp_path / "venus_account_scan_status.md").exists()


def test_bnb_venus_account_scan_status_detects_rpc_retrying(tmp_path):
    (tmp_path / "venus_borrower_registry.json").write_text(
        json.dumps(
            {
                "storage_mode": "file_only",
                "discovery_mode": "borrow_events_only",
                "scan_complete": False,
                "account_count": 1,
                "event_count": 1,
                "events_jsonl": "venus_borrower_events_2mo.jsonl",
                "accounts": [{"account": "0x0000000000000000000000000000000000000001"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "venus_borrower_accounts.txt").write_text("0x0000000000000000000000000000000000000001\n", encoding="utf-8")
    (tmp_path / "venus_borrower_events_2mo.jsonl").write_text(
        json.dumps(
            {
                "account": "0x0000000000000000000000000000000000000001",
                "event": "Borrow",
                "vtoken": "0x0000000000000000000000000000000000000011",
                "block_number": 10,
                "transaction_hash": "0x01",
                "log_index": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "venus_two_month_market_scan_progress.json").write_text(
        json.dumps({"status": "running_market", "market_index": 1, "market_count": 52, "selected_market_count": 52}),
        encoding="utf-8",
    )
    (tmp_path / "venus_borrow_scan_progress.json").write_text(
        json.dumps({"current_from_block": 100, "current_to_block": 200, "error": "all BNB RPCs failed; retrying", "retry_attempt": 2}),
        encoding="utf-8",
    )

    status = build_scan_status(tmp_path, pid=None)

    assert status["status"] == "rpc_retrying"
    assert status["ok"] is True
    assert status["borrow_progress"]["retry_attempt"] == 2
