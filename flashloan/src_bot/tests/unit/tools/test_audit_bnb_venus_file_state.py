import json

from tools.audit_bnb_venus_file_state import audit_file_state, write_audit_report


def test_audit_bnb_venus_file_state_detects_partial_market_window(tmp_path):
    (tmp_path / "venus_borrower_registry.json").write_text(
        json.dumps(
            {
                "storage_mode": "file_only",
                "discovery_mode": "borrow_events_only",
                "events_jsonl": "venus_borrower_events_2mo.jsonl",
                "scan_complete": True,
                "account_count": 1,
                "event_count": 1,
                "accounts": [{"account": "0x0000000000000000000000000000000000000001"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "venus_borrower_accounts.txt").write_text(
        "0x0000000000000000000000000000000000000001\n",
        encoding="utf-8",
    )
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
        json.dumps(
            {
                "status": "completed",
                "market_count": 52,
                "selected_market_count": 1,
                "recent_blocks": 2000,
            }
        ),
        encoding="utf-8",
    )

    result = audit_file_state(tmp_path)

    assert result["status"] == "warning"
    assert result["errors"] == []
    assert "market_scan_incomplete" in result["warnings"]
    assert "recent_blocks_window_only" in result["warnings"]


def test_audit_bnb_venus_file_state_writes_report_files(tmp_path):
    (tmp_path / "venus_borrower_registry.json").write_text(
        json.dumps(
            {
                "storage_mode": "file_only",
                "discovery_mode": "borrow_events_only",
                "events_jsonl": "venus_borrower_events_2mo.jsonl",
                "scan_complete": True,
                "account_count": 1,
                "event_count": 1,
                "accounts": [{"account": "0x0000000000000000000000000000000000000001"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "venus_borrower_accounts.txt").write_text(
        "0x0000000000000000000000000000000000000001\n",
        encoding="utf-8",
    )
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

    result = audit_file_state(tmp_path)
    paths = write_audit_report(tmp_path, result)

    assert (tmp_path / "venus_file_state_audit.json").exists()
    assert (tmp_path / "venus_file_state_audit.md").exists()
    assert paths["json"].endswith("venus_file_state_audit.json")
    assert result["status"] in {"ok", "warning"}
